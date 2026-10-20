"""Pinecone-hosted `bge-reranker-v2-m3` (ADR-0011).

SDK v10 `inference.rerank`, checked 2026-10-20.
"""

from collections.abc import Sequence
from dataclasses import replace

from pinecone import AsyncPinecone, PineconeConnectionError, PineconeTimeoutError, RateLimitError

from kb_assistant.domain.chat import RetrievedChunk
from kb_assistant.domain.errors import ProviderUnavailableError
from kb_assistant.infrastructure.resilience import retrying

_TRANSIENT = (PineconeTimeoutError, PineconeConnectionError, RateLimitError)


class PineconeReranker:
    def __init__(self, client: AsyncPinecone, *, model: str, attempts: int = 3) -> None:
        self._client = client
        self._model = model
        self._attempts = attempts

    @property
    def model_id(self) -> str:
        return self._model

    async def rerank(
        self, query: str, chunks: Sequence[RetrievedChunk], top_n: int
    ) -> list[RetrievedChunk]:
        if not chunks:
            return []
        try:
            async for attempt in retrying(attempts=self._attempts, retry_on=_TRANSIENT):
                with attempt:
                    result = await self._client.inference.rerank(
                        model=self._model,
                        query=query,
                        documents=[{"id": c.chunk.vector_id, "text": c.chunk.text} for c in chunks],
                        rank_fields=["text"],
                        return_documents=False,
                        top_n=min(top_n, len(chunks)),
                    )
        except _TRANSIENT as exc:
            raise ProviderUnavailableError("reranker", type(exc).__name__) from exc
        return [replace(chunks[item.index], rerank_score=float(item.score)) for item in result.data]

    async def aclose(self) -> None:
        await self._client.close()
