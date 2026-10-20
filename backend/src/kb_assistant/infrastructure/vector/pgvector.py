"""pgvector adapter: local development, CI, and the offline demo.

The table is protected by the same row-level security as the rest of the
schema, so the adapter binds `app.tenant_id` for each transaction: the tenant
column plays the role of Pinecone's namespace, enforced by the database.

HNSW with a filter is post-filtered: with a narrow scope the index could
return fewer than `top_k` rows. pgvector 0.8 iterative scans fix that; the
setting is transaction-local.
"""

from collections import defaultdict
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from kb_assistant.domain.documents import RetrievalScope, VectorMatch, VectorRecord
from kb_assistant.domain.errors import ProviderUnavailableError
from kb_assistant.domain.ids import TenantId, VersionId
from kb_assistant.infrastructure.db.tables import VectorRecordRow

_BIND_TENANT = text("SELECT set_config('app.tenant_id', :tenant_id, true)")
_ITERATIVE_SCAN = text("SET LOCAL hnsw.iterative_scan = relaxed_order")
UPSERT_BATCH = 500


class PgVectorStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @asynccontextmanager
    async def _tenant(self, tenant_id: TenantId) -> AsyncIterator[AsyncConnection]:
        try:
            async with self._engine.begin() as conn:
                await conn.execute(_BIND_TENANT, {"tenant_id": str(tenant_id)})
                yield conn
        except (OperationalError, OSError) as exc:
            raise ProviderUnavailableError(
                "vector_store", "The vector store is unavailable."
            ) from exc
        except DBAPIError as exc:
            if exc.connection_invalidated:
                raise ProviderUnavailableError("vector_store", "Connection lost.") from exc
            raise

    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        by_tenant: dict[TenantId, list[VectorRecord]] = defaultdict(list)
        for record in records:
            by_tenant[record.tenant_id].append(record)
        for tenant_id, tenant_records in by_tenant.items():
            async with self._tenant(tenant_id) as conn:
                for start in range(0, len(tenant_records), UPSERT_BATCH):
                    rows = [
                        {
                            "id": r.id,
                            "tenant_id": r.tenant_id,
                            "department_id": r.department_id,
                            "document_id": r.document_id,
                            "version_id": r.version_id,
                            "chunk_index": r.chunk_index,
                            "page": r.page,
                            "is_active": True,
                            "embedding": list(r.values),
                        }
                        for r in tenant_records[start : start + UPSERT_BATCH]
                    ]
                    statement = insert(VectorRecordRow).values(rows)
                    await conn.execute(
                        statement.on_conflict_do_update(
                            index_elements=[VectorRecordRow.id],
                            set_={
                                "embedding": statement.excluded.embedding,
                                "is_active": True,
                                "page": statement.excluded.page,
                                "department_id": statement.excluded.department_id,
                            },
                        )
                    )

    async def deactivate_version(self, tenant_id: TenantId, version_id: VersionId) -> None:
        async with self._tenant(tenant_id) as conn:
            await conn.execute(
                update(VectorRecordRow)
                .where(
                    VectorRecordRow.tenant_id == tenant_id,
                    VectorRecordRow.version_id == version_id,
                )
                .values(is_active=False)
            )

    async def query(
        self, vector: Sequence[float], scope: RetrievalScope, top_k: int
    ) -> list[VectorMatch]:
        if not scope.department_ids or top_k < 1:
            return []
        distance = VectorRecordRow.embedding.cosine_distance(list(vector))
        async with self._tenant(scope.tenant_id) as conn:
            await conn.execute(_ITERATIVE_SCAN)
            rows = await conn.execute(
                select(VectorRecordRow.id, (1 - distance).label("score"))
                .where(
                    VectorRecordRow.tenant_id == scope.tenant_id,
                    VectorRecordRow.department_id.in_(sorted(scope.department_ids)),
                    VectorRecordRow.is_active.is_(True),
                )
                .order_by(distance)
                .limit(top_k)
            )
            return [VectorMatch(id=row.id, score=float(row.score)) for row in rows]
