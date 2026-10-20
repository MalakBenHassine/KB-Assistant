"""Conversations and questions.

`POST /v1/conversations/{id}/messages` streams server-sent events when the
client asks for `text/event-stream`, and returns the final answer as JSON
otherwise. Authorization and validation errors are raised before the first
byte, so they are ordinary RFC 7807 responses, never a broken stream.

Events: `meta` (message and request ids), `status` (retrieving, generating),
`sentence` (one validated sentence and its citation aliases), `message` (a
refusal or abstention), `done` (the full answer with resolved citations).
"""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse, Response, StreamingResponse

from kb_assistant.api.chat_schemas import (
    AnswerOut,
    AskRequest,
    ConversationCreate,
    ConversationOut,
    MessageOut,
)
from kb_assistant.api.deps import ContainerDep, PrincipalDep, UowDep
from kb_assistant.api.schemas import Page
from kb_assistant.application.answering.service import (
    AnswerEvent,
    CreateConversation,
    ListConversations,
    ListMessages,
)
from kb_assistant.domain.ids import ConversationId, DepartmentId

router = APIRouter(prefix="/v1/conversations", tags=["chat"])
log = logging.getLogger(__name__)

_STREAM_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
_ASK_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": (
            "The answer: an SSE stream, or JSON (AnswerOut) when the client does not"
            " send Accept: text/event-stream"
        ),
        "content": {"text/event-stream": {}},
    },
    403: {"description": "A requested department is outside the caller's access"},
    404: {"description": "Not the caller's conversation"},
}


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


async def _event_stream(
    events: AsyncIterator[AnswerEvent], request_id: str
) -> AsyncIterator[bytes]:
    try:
        async for event in events:
            yield _sse(event.kind, event.data)
    except Exception:
        log.exception("chat.stream_failed")
        yield _sse(
            "error",
            {
                "type": "urn:kb-assistant:problem:internal_error",
                "detail": "The answer could not be completed.",
                "request_id": request_id,
            },
        )


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: ConversationCreate, principal: PrincipalDep, uow: UowDep, c: ContainerDep
) -> ConversationOut:
    return ConversationOut.of(await CreateConversation(uow, c.clock).execute(principal, body.title))


@router.get("", response_model=Page[ConversationOut])
async def list_conversations(principal: PrincipalDep, uow: UowDep) -> Page[ConversationOut]:
    conversations = await ListConversations(uow).execute(principal)
    return Page(items=[ConversationOut.of(c) for c in conversations])


@router.get("/{conversation_id}/messages", response_model=Page[MessageOut])
async def list_messages(
    conversation_id: UUID, principal: PrincipalDep, uow: UowDep
) -> Page[MessageOut]:
    messages = await ListMessages(uow).execute(principal, ConversationId(conversation_id))
    return Page(items=[MessageOut.of(m) for m in messages])


@router.post("/{conversation_id}/messages", response_model=AnswerOut, responses=_ASK_RESPONSES)
async def ask(
    conversation_id: UUID,
    body: AskRequest,
    request: Request,
    principal: PrincipalDep,
    c: ContainerDep,
) -> Response:
    request_id: str = request.state.request_id
    use_case = c.ask()
    prepared = await use_case.prepare(
        principal,
        ConversationId(conversation_id),
        body.question,
        frozenset(DepartmentId(d) for d in body.department_ids)
        if body.department_ids is not None
        else None,
        request_id,
    )
    if "text/event-stream" in request.headers.get("accept", ""):
        return StreamingResponse(
            _event_stream(use_case.stream(prepared), request_id),
            media_type="text/event-stream",
            headers=_STREAM_HEADERS,
        )
    final: dict[str, Any] = {}
    async for event in use_case.stream(prepared):
        if event.kind == "done":
            final = event.data
    return JSONResponse(AnswerOut.model_validate(final).model_dump(mode="json"))
