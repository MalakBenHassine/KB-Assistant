"""Questions, retrieved evidence, answers and the conversations that hold them."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from kb_assistant.domain.ids import (
    ChunkId,
    ConversationId,
    DepartmentId,
    DocumentId,
    MembershipId,
    MessageId,
    TenantId,
    VersionId,
)


class Intent(StrEnum):
    KB_QUESTION = "kb_question"
    OUT_OF_SCOPE = "out_of_scope"
    CHITCHAT = "chitchat"


class RetrievalGrade(StrEnum):
    GOOD = "good"
    WEAK = "weak"
    EMPTY = "empty"


class AbstainReason(StrEnum):
    NO_ACCESSIBLE_DOCUMENTS = "no_accessible_documents"  # the scope is empty
    NO_RELEVANT_DOCUMENTS = "no_relevant_documents"  # nothing retrieved
    WEAK_EVIDENCE = "weak_evidence"  # retrieved, not convincing: clarification asked
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"  # the model found no answer in the sources
    UNSUPPORTED_ANSWER = "unsupported_answer"  # every sentence failed citation validation
    ASSISTANT_UNAVAILABLE = "assistant_unavailable"  # the language model is down
    NOT_A_KNOWLEDGE_QUESTION = "not_a_knowledge_question"


@dataclass(frozen=True, slots=True)
class HydratedChunk:
    """A chunk as stored in Postgres, after the second isolation check."""

    chunk_id: ChunkId
    vector_id: str
    document_id: DocumentId
    version_id: VersionId
    department_id: DepartmentId
    document_title: str
    text: str
    heading_path: tuple[str, ...]
    page_start: int | None
    page_end: int | None


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk: HydratedChunk
    score: float  # vector similarity
    rerank_score: float | None = None


@dataclass(frozen=True, slots=True)
class Citation:
    alias: str  # what the model saw and wrote ("S2")
    chunk_id: ChunkId
    document_id: DocumentId
    document_title: str
    heading_path: tuple[str, ...]
    page_start: int | None
    page_end: int | None


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens
        )


@dataclass(frozen=True, slots=True)
class JsonSchema:
    """A structured-output contract, kept framework-free on purpose."""

    name: str
    schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class StructuredReply:
    value: dict[str, Any]
    usage: TokenUsage


@dataclass(frozen=True, slots=True)
class StreamChunk:
    """A text delta, or (last) the usage of the whole stream."""

    text: str = ""
    usage: TokenUsage | None = None


@dataclass(frozen=True, slots=True)
class Conversation:
    id: ConversationId
    tenant_id: TenantId
    membership_id: MembershipId
    title: str
    summary: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Message:
    id: MessageId
    tenant_id: TenantId
    conversation_id: ConversationId
    role: str  # "user" | "assistant"
    content: str
    created_at: datetime
    request_id: str | None = None
    intent: Intent | None = None
    grade: RetrievalGrade | None = None
    abstained: bool = False
    abstain_reason: AbstainReason | None = None
    retrieved_chunk_ids: tuple[ChunkId, ...] = ()
    cited_chunk_ids: tuple[ChunkId, ...] = ()
    citations: tuple[Citation, ...] = ()
    scores: dict[str, float] = field(default_factory=dict)
    model: str | None = None
    prompt_version: str | None = None
    thresholds: dict[str, Any] = field(default_factory=dict)
    token_usage: TokenUsage = TokenUsage()
    latency_ms: int | None = None
