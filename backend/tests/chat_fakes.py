"""Fakes for the answering workflow. Each one records what it was asked."""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from kb_assistant.domain.chat import (
    HydratedChunk,
    JsonSchema,
    RetrievedChunk,
    StreamChunk,
    StructuredReply,
    TokenUsage,
)
from kb_assistant.domain.documents import RetrievalScope, VectorMatch, VectorRecord
from kb_assistant.domain.errors import ProviderUnavailableError
from kb_assistant.domain.ids import ChunkId, DepartmentId, DocumentId, TenantId, VersionId


def chunk(text: str, department: DepartmentId, title: str = "Handbook") -> HydratedChunk:
    version = VersionId(uuid4())
    return HydratedChunk(
        chunk_id=ChunkId(uuid4()),
        vector_id=f"{version}:0",
        document_id=DocumentId(uuid4()),
        version_id=version,
        department_id=department,
        document_title=title,
        text=text,
        heading_path=("Policy",),
        page_start=1,
        page_end=1,
    )


@dataclass
class SpyVectorStore:
    """Returns preset matches and records every query (scope included)."""

    matches: list[VectorMatch] = field(default_factory=list)
    queries: list[tuple[RetrievalScope, int]] = field(default_factory=list)
    fail: bool = False

    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        raise NotImplementedError

    async def deactivate_version(self, tenant_id: TenantId, version_id: VersionId) -> None:
        raise NotImplementedError

    async def query(
        self, vector: Sequence[float], scope: RetrievalScope, top_k: int
    ) -> list[VectorMatch]:
        self.queries.append((scope, top_k))
        if self.fail:
            raise ProviderUnavailableError("vector_store", "down")
        return list(self.matches)


@dataclass
class FakeHydrator:
    """Stands in for the Postgres re-check: only `known` chunks come back."""

    known: dict[str, HydratedChunk] = field(default_factory=dict)
    calls: list[tuple[list[str], RetrievalScope]] = field(default_factory=list)

    async def __call__(self, vector_ids: list[str], scope: RetrievalScope) -> list[HydratedChunk]:
        self.calls.append((vector_ids, scope))
        return [
            self.known[v]
            for v in vector_ids
            if v in self.known and self.known[v].department_id in scope.department_ids
        ]


class ScriptedChatModel:
    """Answers structured calls from `replies`, streams `answer`; records every prompt."""

    def __init__(
        self,
        *,
        answer: str = "",
        replies: dict[str, dict[str, Any]] | None = None,
        unavailable: set[str] | None = None,
    ) -> None:
        self.answer = answer
        self.replies = replies or {}
        self.unavailable = unavailable or set()
        self.structured_calls: list[tuple[str, str]] = []  # (schema name, user prompt)
        self.stream_prompts: list[str] = []

    @property
    def model_id(self) -> str:
        return "scripted"

    async def structured(
        self, *, system: str, user: str, schema: JsonSchema, max_output_tokens: int
    ) -> StructuredReply:
        self.structured_calls.append((schema.name, user))
        if schema.name in self.unavailable:
            raise ProviderUnavailableError("language_model", "down")
        default: dict[str, dict[str, Any]] = {
            "intent": {"intent": "kb_question"},
            "rewrite": {"query": "rewritten"},
            "relevance": {"relevant": True},
        }
        return StructuredReply(
            self.replies.get(schema.name, default[schema.name]), TokenUsage(3, 1)
        )

    async def stream(
        self, *, system: str, user: str, max_output_tokens: int
    ) -> AsyncIterator[StreamChunk]:
        self.stream_prompts.append(user)
        if "answer" in self.unavailable:
            raise ProviderUnavailableError("language_model", "down")
        for start in range(0, len(self.answer), 7):
            yield StreamChunk(text=self.answer[start : start + 7])
        yield StreamChunk(usage=TokenUsage(100, 20))


@dataclass
class ReverseReranker:
    calls: int = 0
    fail: bool = False

    @property
    def model_id(self) -> str:
        return "reverse"

    async def rerank(
        self, query: str, chunks: Sequence[RetrievedChunk], top_n: int
    ) -> list[RetrievedChunk]:
        self.calls += 1
        if self.fail:
            raise ProviderUnavailableError("reranker", "down")
        return list(reversed(chunks))[:top_n]


class ConstantEmbedder:
    @property
    def model_id(self) -> str:
        return "constant"

    @property
    def dimension(self) -> int:
        return 3

    async def embed_documents(self, texts: Sequence[str]) -> Any:
        raise NotImplementedError

    async def embed_query(self, text: str) -> tuple[float, ...]:
        return (1.0, 0.0, 0.0)
