"""Wire format for conversations and answers.

The request carries no tenant and no role. It may name departments, which can
only narrow the search (a department the caller cannot read is a 403).
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from kb_assistant.api.schemas import RequestModel
from kb_assistant.application.answering.service import MAX_QUESTION_LENGTH
from kb_assistant.domain.chat import Citation, Conversation, Message


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class ConversationCreate(RequestModel):
    title: Annotated[str | None, Field(max_length=200)] = None


class AskRequest(RequestModel):
    question: Annotated[str, Field(min_length=1, max_length=MAX_QUESTION_LENGTH)]
    department_ids: Annotated[list[UUID] | None, Field(max_length=50)] = None


class ConversationOut(_Out):
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, c: Conversation) -> "ConversationOut":
        return cls(id=c.id, title=c.title, created_at=c.created_at, updated_at=c.updated_at)


class CitationOut(_Out):
    alias: str
    chunk_id: UUID
    document_id: UUID
    document_title: str
    heading_path: list[str]
    page_start: int | None
    page_end: int | None

    @classmethod
    def of(cls, c: Citation) -> "CitationOut":
        return cls(
            alias=c.alias,
            chunk_id=c.chunk_id,
            document_id=c.document_id,
            document_title=c.document_title,
            heading_path=list(c.heading_path),
            page_start=c.page_start,
            page_end=c.page_end,
        )


class MessageOut(_Out):
    id: UUID
    role: str
    content: str
    created_at: datetime
    citations: list[CitationOut]
    abstained: bool
    abstain_reason: str | None
    grade: str | None

    @classmethod
    def of(cls, m: Message) -> "MessageOut":
        return cls(
            id=m.id,
            role=m.role,
            content=m.content,
            created_at=m.created_at,
            citations=[CitationOut.of(c) for c in m.citations],
            abstained=m.abstained,
            abstain_reason=m.abstain_reason.value if m.abstain_reason else None,
            grade=m.grade.value if m.grade else None,
        )


class AnswerOut(_Out):
    """The final `done` event, also returned as JSON when the client does not stream."""

    message_id: UUID
    answer: str
    citations: list[CitationOut]
    abstained: bool
    abstain_reason: str | None
    needs_clarification: bool
    grade: str | None
    latency_ms: int
