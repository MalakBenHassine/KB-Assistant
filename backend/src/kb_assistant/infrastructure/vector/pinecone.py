"""Pinecone adapter (SDK v10, `AsyncIndex`; API checked 2026-10-20).

- One namespace per tenant; the name is derived here from the scope.
- Metadata holds identifiers and filter fields only, never document text
  (ARCHITECTURE §6.3): the text is hydrated from Postgres.
- Archiving a version is one metadata update by filter, not one call per vector.
- Every match is checked against the scope's tenant before it is returned:
  a mis-routed upsert would be caught and logged instead of trusted.
"""

import logging
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from pinecone import AsyncIndex, PineconeConnectionError, PineconeTimeoutError, RateLimitError

from kb_assistant.domain.documents import RetrievalScope, VectorMatch, VectorRecord
from kb_assistant.domain.errors import ProviderUnavailableError
from kb_assistant.domain.ids import TenantId, VersionId
from kb_assistant.infrastructure.resilience import retrying

log = logging.getLogger(__name__)

UPSERT_BATCH = 100
_TRANSIENT = (PineconeTimeoutError, PineconeConnectionError, RateLimitError)


def namespace_for(tenant_id: TenantId) -> str:
    return f"t_{tenant_id}"


def filter_for(scope: RetrievalScope) -> dict[str, Any]:
    return {
        "$and": [
            {"department_id": {"$in": sorted(str(d) for d in scope.department_ids)}},
            {"is_active": {"$eq": True}},
        ]
    }


def _metadata(record: VectorRecord) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "tenant_id": str(record.tenant_id),
        "department_id": str(record.department_id),
        "document_id": str(record.document_id),
        "version_id": str(record.version_id),
        "chunk_index": record.chunk_index,
        "is_active": True,
    }
    if record.page is not None:  # Pinecone rejects null metadata values
        metadata["page"] = record.page
    return metadata


class PineconeVectorStore:
    def __init__(self, index: AsyncIndex, *, attempts: int = 4, timeout_s: float = 10.0) -> None:
        self._index = index
        self._attempts = attempts
        self._timeout_s = timeout_s

    async def _call(self, operation: str, coro_factory: Any) -> Any:
        try:
            async for attempt in retrying(attempts=self._attempts, retry_on=_TRANSIENT):
                with attempt:
                    return await coro_factory()
        except _TRANSIENT as exc:
            raise ProviderUnavailableError("vector_store", f"Pinecone {operation} failed.") from exc
        return None  # pragma: no cover - retrying either returns or raises

    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        by_tenant: dict[TenantId, list[VectorRecord]] = defaultdict(list)
        for record in records:
            by_tenant[record.tenant_id].append(record)
        for tenant_id, tenant_records in by_tenant.items():
            vectors = [
                {"id": r.id, "values": list(r.values), "metadata": _metadata(r)}
                for r in tenant_records
            ]
            await self._call(
                "upsert",
                lambda vectors=vectors, tenant_id=tenant_id: self._index.upsert(
                    vectors=vectors,
                    namespace=namespace_for(tenant_id),
                    batch_size=UPSERT_BATCH,
                    show_progress=False,
                    timeout=self._timeout_s,
                ),
            )

    async def deactivate_version(self, tenant_id: TenantId, version_id: VersionId) -> None:
        await self._call(
            "update",
            lambda: self._index.update(
                filter={"version_id": {"$eq": str(version_id)}},
                set_metadata={"is_active": False},
                namespace=namespace_for(tenant_id),
                timeout=self._timeout_s,
            ),
        )

    async def query(
        self, vector: Sequence[float], scope: RetrievalScope, top_k: int
    ) -> list[VectorMatch]:
        if not scope.department_ids or top_k < 1:
            return []  # `$in: []` has provider-specific meaning; never send it
        response = await self._call(
            "query",
            lambda: self._index.query(
                vector=list(vector),
                top_k=top_k,
                namespace=namespace_for(scope.tenant_id),
                filter=filter_for(scope),
                include_metadata=True,
                timeout=self._timeout_s,
            ),
        )
        matches: list[VectorMatch] = []
        for match in response.matches:
            metadata = match.metadata or {}
            if metadata.get("tenant_id") != str(scope.tenant_id):
                log.error("vector_store.foreign_tenant_match", extra={"vector_id": match.id})
                continue
            matches.append(VectorMatch(id=match.id, score=float(match.score)))
        return matches

    async def aclose(self) -> None:
        await self._index.close()
