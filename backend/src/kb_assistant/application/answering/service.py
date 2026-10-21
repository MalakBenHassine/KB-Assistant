"""Use cases around the graph: scope, conversations, asking a question.

`AskQuestion` is split in two so that authorization errors are ordinary HTTP
errors and not a stream that dies after its first byte:
- `prepare()` checks the conversation and the requested scope, stores the
  question, and raises (404, 403) *before* any response is sent;
- `stream()` runs the graph and yields events, then stores the answer with
  everything needed to explain it later (ARCHITECTURE §1, goal 3).
"""

import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any

from kb_assistant.application.answering.graph import (
    MESSAGES,
    AnswerPolicy,
    GraphState,
    LlmBudgetExceededError,
    recursion_limit,
)
from kb_assistant.application.answering.prompts import PROMPT_VERSION
from kb_assistant.domain.chat import (
    AbstainReason,
    Citation,
    Conversation,
    HydratedChunk,
    Message,
    RetrievedChunk,
    TokenUsage,
)
from kb_assistant.domain.documents import RetrievalScope
from kb_assistant.domain.errors import ForbiddenError, NotFoundError, PolicyViolationError
from kb_assistant.domain.ids import ConversationId, DepartmentId, MessageId, new_id
from kb_assistant.domain.models import AuditEvent, Role
from kb_assistant.domain.ports import Clock, UnitOfWork
from kb_assistant.domain.principal import Principal

log = logging.getLogger(__name__)

UnitOfWorkFactory = Callable[[], AbstractAsyncContextManager[UnitOfWork]]
MAX_QUESTION_LENGTH = 2_000
MAX_TITLE_LENGTH = 200


async def retrieval_scope(
    uow: UnitOfWork, principal: Principal, requested: frozenset[DepartmentId] | None
) -> RetrievalScope:
    """The only way to build a `RetrievalScope` from a request (ARCHITECTURE §6.2).

    A requested department list can narrow the search, never widen it: asking
    for a department the caller cannot read is a 403, not a silent fallback.
    """
    if principal.role is Role.TENANT_ADMIN:
        allowed = frozenset(d.id for d in await uow.departments.list_all())
    else:
        allowed = principal.department_ids
    if requested is None:
        return RetrievalScope(tenant_id=principal.tenant_id, department_ids=allowed)
    if not requested:
        raise PolicyViolationError("Name at least one department, or none to search them all.")
    if not requested <= allowed:
        raise ForbiddenError("You can only search departments you belong to.")
    return RetrievalScope(tenant_id=principal.tenant_id, department_ids=requested)


async def hydrate(
    uow_factory: UnitOfWorkFactory, vector_ids: list[str], scope: RetrievalScope
) -> list[HydratedChunk]:
    """Isolation check 2: re-read the hits from Postgres under the same scope."""
    if not vector_ids:
        return []
    async with uow_factory() as uow:
        await uow.bind(tenant_id=scope.tenant_id, user_id=None)
        return await uow.chunks.hydrate(vector_ids, sorted(scope.department_ids))


# ----------------------------------------------------------------------------- conversations


async def _own_conversation(
    uow: UnitOfWork, principal: Principal, conversation_id: ConversationId
) -> Conversation:
    conversation = await uow.conversations.get(conversation_id)
    if conversation is None or conversation.membership_id != principal.membership_id:
        raise NotFoundError("Conversation not found.")
    return conversation


class CreateConversation:
    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def execute(self, principal: Principal, title: str | None) -> Conversation:
        now = self._clock.now()
        conversation = Conversation(
            id=ConversationId(new_id()),
            tenant_id=principal.tenant_id,
            membership_id=principal.membership_id,
            title=(title or "New conversation").strip()[:MAX_TITLE_LENGTH] or "New conversation",
            summary=None,
            created_at=now,
            updated_at=now,
        )
        await self._uow.conversations.add(conversation)
        await self._uow.commit()
        return conversation


class ListConversations:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def execute(self, principal: Principal) -> list[Conversation]:
        return await self._uow.conversations.list_for_member(principal.membership_id)


class ListMessages:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def execute(self, principal: Principal, conversation_id: ConversationId) -> list[Message]:
        await _own_conversation(self._uow, principal, conversation_id)
        return await self._uow.messages.list_for_conversation(conversation_id)


# ----------------------------------------------------------------------------- asking


@dataclass(frozen=True, slots=True)
class AnswerEvent:
    kind: str  # "meta" | "status" | "sentence" | "message" | "done"
    data: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PreparedQuestion:
    principal: Principal
    conversation: Conversation
    scope: RetrievalScope
    question: str
    request_id: str
    user_message_id: MessageId
    started: float = field(default_factory=time.perf_counter)


def initial_state(
    *,
    request_id: str,
    scope: RetrievalScope,
    question: str,
    conversation_summary: str | None,
) -> GraphState:
    """The graph's input; the API and the evaluation both start from it."""
    return {
        "request_id": request_id,
        "scope": scope,
        "question": question,
        "conversation_summary": conversation_summary,
        "retry_count": 0,
        "llm_calls": 0,
        "node_timings_ms": {},
        "token_usage": TokenUsage(),
    }


def citation_payload(citation: Citation) -> dict[str, Any]:
    return {
        "alias": citation.alias,
        "chunk_id": str(citation.chunk_id),
        "document_id": str(citation.document_id),
        "document_title": citation.document_title,
        "heading_path": list(citation.heading_path),
        "page_start": citation.page_start,
        "page_end": citation.page_end,
    }


class AskQuestion:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        graph: Any,
        policy: AnswerPolicy,
        clock: Clock,
        model_id: str,
    ) -> None:
        self._uow_factory = uow_factory
        self._graph = graph
        self._policy = policy
        self._clock = clock
        self._model_id = model_id

    async def prepare(
        self,
        principal: Principal,
        conversation_id: ConversationId,
        question: str,
        requested_departments: frozenset[DepartmentId] | None,
        request_id: str,
    ) -> PreparedQuestion:
        question = question.strip()
        if not question:
            raise PolicyViolationError("The question is empty.")
        if len(question) > MAX_QUESTION_LENGTH:
            raise PolicyViolationError(
                f"Questions are limited to {MAX_QUESTION_LENGTH} characters."
            )
        async with self._uow_factory() as uow:
            await uow.bind(tenant_id=principal.tenant_id, user_id=principal.user_id)
            conversation = await _own_conversation(uow, principal, conversation_id)
            scope = await retrieval_scope(uow, principal, requested_departments)
            message_id = MessageId(new_id())
            await uow.messages.add(
                Message(
                    id=message_id,
                    tenant_id=principal.tenant_id,
                    conversation_id=conversation.id,
                    role="user",
                    content=question,
                    created_at=self._clock.now(),
                    request_id=request_id,
                )
            )
            await uow.commit()
        return PreparedQuestion(
            principal=principal,
            conversation=conversation,
            scope=scope,
            question=question,
            request_id=request_id,
            user_message_id=message_id,
        )

    async def stream(self, prepared: PreparedQuestion) -> AsyncIterator[AnswerEvent]:
        answer_id = MessageId(new_id())
        yield AnswerEvent("meta", {"message_id": str(answer_id), "request_id": prepared.request_id})
        initial = initial_state(
            request_id=prepared.request_id,
            scope=prepared.scope,
            question=prepared.question,
            conversation_summary=prepared.conversation.summary,
        )
        final: dict[str, Any] = dict(initial)
        try:
            async for mode, payload in self._graph.astream(
                initial,
                stream_mode=["custom", "values"],
                config={"recursion_limit": recursion_limit(self._policy)},
            ):
                if mode == "custom":
                    yield AnswerEvent(
                        payload["type"], {k: v for k, v in payload.items() if k != "type"}
                    )
                else:
                    final = payload
        except LlmBudgetExceededError:
            final["abstained"] = True
            final["abstain_reason"] = AbstainReason.ASSISTANT_UNAVAILABLE
            final["answer"] = MESSAGES[AbstainReason.ASSISTANT_UNAVAILABLE]
            yield AnswerEvent("message", {"text": final["answer"]})

        latency_ms = int((time.perf_counter() - prepared.started) * 1000)
        message = self._assistant_message(prepared, answer_id, final, latency_ms)
        await self._persist(prepared, message, final)
        yield AnswerEvent(
            "done",
            {
                "message_id": str(answer_id),
                "answer": message.content,
                "citations": [citation_payload(c) for c in message.citations],
                "abstained": message.abstained,
                "abstain_reason": message.abstain_reason.value if message.abstain_reason else None,
                "needs_clarification": bool(final.get("needs_clarification")),
                "grade": message.grade.value if message.grade else None,
                "latency_ms": latency_ms,
            },
        )

    def _assistant_message(
        self,
        prepared: PreparedQuestion,
        answer_id: MessageId,
        final: dict[str, Any],
        latency_ms: int,
    ) -> Message:
        retrieved: list[RetrievedChunk] = final.get("retrieved_chunks") or []
        citations: list[Citation] = final.get("citations") or []
        return Message(
            id=answer_id,
            tenant_id=prepared.principal.tenant_id,
            conversation_id=prepared.conversation.id,
            role="assistant",
            content=final.get("answer") or MESSAGES[AbstainReason.UNSUPPORTED_ANSWER],
            created_at=self._clock.now(),
            request_id=prepared.request_id,
            intent=final.get("intent"),
            grade=final.get("retrieval_grade"),
            abstained=bool(final.get("abstained")),
            abstain_reason=final.get("abstain_reason"),
            retrieved_chunk_ids=tuple(c.chunk.chunk_id for c in retrieved),
            cited_chunk_ids=tuple(c.chunk_id for c in citations),
            citations=tuple(citations),
            scores={str(c.chunk.chunk_id): round(c.score, 6) for c in retrieved},
            model=self._model_id,
            prompt_version=PROMPT_VERSION,
            thresholds=self._policy.thresholds(),
            token_usage=final.get("token_usage") or TokenUsage(),
            latency_ms=latency_ms,
        )

    async def _persist(
        self, prepared: PreparedQuestion, message: Message, final: dict[str, Any]
    ) -> None:
        async with self._uow_factory() as uow:
            await uow.bind(
                tenant_id=prepared.principal.tenant_id, user_id=prepared.principal.user_id
            )
            await uow.messages.add(message)
            await uow.conversations.touch(prepared.conversation.id, self._clock.now())
            await uow.audit.record(
                AuditEvent(
                    tenant_id=prepared.principal.tenant_id,
                    action="chat.question",
                    actor_user_id=prepared.principal.user_id,
                    target=str(message.id),
                    details={
                        "question": prepared.question,
                        "retrieved_chunk_ids": [str(i) for i in message.retrieved_chunk_ids],
                        "cited_chunk_ids": [str(i) for i in message.cited_chunk_ids],
                        "departments": sorted(str(d) for d in prepared.scope.department_ids),
                        "abstained": message.abstained,
                        "hydration_rejections": final.get("hydration_rejections", 0),
                    },
                )
            )
            await uow.commit()
