"""Hand-written fakes for the ports (ADR-0006: fakes are first-class adapters)."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from kb_assistant.domain.documents import EmbeddingBatch
from kb_assistant.domain.errors import ProviderUnavailableError
from kb_assistant.domain.ids import TenantId, VersionId
from kb_assistant.infrastructure.embeddings import HashingEmbedder


@dataclass
class RecordingJobQueue:
    """Collects enqueued jobs; tests decide when (and whether) the worker runs them."""

    jobs: list[tuple[TenantId, VersionId]] = field(default_factory=list)

    async def enqueue_ingestion(self, tenant_id: TenantId, version_id: VersionId) -> None:
        self.jobs.append((tenant_id, version_id))

    def take(self) -> list[tuple[TenantId, VersionId]]:
        taken, self.jobs = self.jobs, []
        return taken


class WordTokenizer:
    """One token per whitespace-separated word: makes chunking tests easy to reason about."""

    def count(self, text: str) -> int:
        return len(text.split())

    def split(self, text: str, max_tokens: int) -> list[str]:
        words = text.split()
        return [" ".join(words[i : i + max_tokens]) for i in range(0, len(words), max_tokens)]

    def tail(self, text: str, max_tokens: int) -> str:
        return " ".join(text.split()[-max_tokens:])


class FlakyEmbedder:
    """Fails the first `failures` calls as an outage would, then behaves."""

    def __init__(self, dimension: int, failures: int) -> None:
        self._inner = HashingEmbedder(dimension)
        self.failures = failures
        self.calls = 0

    @property
    def model_id(self) -> str:
        return self._inner.model_id

    @property
    def dimension(self) -> int:
        return self._inner.dimension

    async def embed_documents(self, texts: Sequence[str]) -> EmbeddingBatch:
        self.calls += 1
        if self.calls <= self.failures:
            raise ProviderUnavailableError("embedding_provider", "simulated outage")
        return await self._inner.embed_documents(texts)

    async def embed_query(self, text: str) -> tuple[float, ...]:
        return await self._inner.embed_query(text)


@dataclass
class _Match:
    id: str
    score: float
    metadata: dict[str, Any] | None


@dataclass
class _QueryResponse:
    matches: list[_Match]


@dataclass
class FakePineconeIndex:
    """Records every call, so tests can assert on what would be sent to Pinecone."""

    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    matches: list[_Match] = field(default_factory=list)

    async def upsert(self, **kwargs: Any) -> None:
        self.calls.append(("upsert", kwargs))

    async def update(self, **kwargs: Any) -> None:
        self.calls.append(("update", kwargs))

    async def query(self, **kwargs: Any) -> _QueryResponse:
        self.calls.append(("query", kwargs))
        return _QueryResponse(matches=list(self.matches))

    async def close(self) -> None:
        self.calls.append(("close", {}))

    def add_match(self, vector_id: str, score: float, tenant_id: str) -> None:
        self.matches.append(_Match(vector_id, score, {"tenant_id": tenant_id}))
