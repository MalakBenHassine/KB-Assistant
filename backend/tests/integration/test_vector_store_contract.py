"""The contract every VectorStore adapter must honour (ADR-0002).

Runs against pgvector on every build. Runs against Pinecone too when
`PINECONE_API_KEY` and `PINECONE_INDEX_HOST` are set (a nightly job with the
secret); a 1536-dimension cosine index is expected there.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from kb_assistant.domain.documents import RetrievalScope, VectorRecord
from kb_assistant.domain.ids import DepartmentId, DocumentId, TenantId, VersionId
from kb_assistant.domain.ports import VectorStore
from kb_assistant.infrastructure.embeddings import HashingEmbedder
from kb_assistant.infrastructure.vector.pgvector import PgVectorStore
from tests.support import Seeder

DIMENSION = 1536
EMBEDDER = HashingEmbedder(DIMENSION)
PINECONE = bool(os.environ.get("PINECONE_API_KEY") and os.environ.get("PINECONE_INDEX_HOST"))


@pytest.fixture(
    params=[
        "pgvector",
        pytest.param(
            "pinecone",
            marks=pytest.mark.skipif(
                not PINECONE, reason="PINECONE_API_KEY and PINECONE_INDEX_HOST not set"
            ),
        ),
    ]
)
async def store(
    request: pytest.FixtureRequest, app_engine: AsyncEngine
) -> AsyncIterator[VectorStore]:
    if request.param == "pgvector":
        yield PgVectorStore(app_engine)
        return
    from pinecone import AsyncIndex

    from kb_assistant.infrastructure.vector.pinecone import PineconeVectorStore

    pinecone = PineconeVectorStore(
        AsyncIndex(host=os.environ["PINECONE_INDEX_HOST"], api_key=os.environ["PINECONE_API_KEY"])
    )
    yield pinecone
    await pinecone.aclose()


async def _record(
    tenant: TenantId, department: DepartmentId, version: VersionId, index: int, text: str
) -> VectorRecord:
    return VectorRecord(
        id=f"{version}:{index}",
        values=await EMBEDDER.embed_query(text),
        tenant_id=tenant,
        department_id=department,
        document_id=DocumentId(uuid4()),
        version_id=version,
        chunk_index=index,
        page=None,
    )


async def _query(store: VectorStore, text: str, scope: RetrievalScope, top_k: int = 5) -> list[str]:
    """Query once on pgvector; poll briefly on Pinecone, whose writes are eventually consistent."""
    vector = await EMBEDDER.embed_query(text)
    attempts = 1 if isinstance(store, PgVectorStore) else 20
    matches = []
    for _ in range(attempts):
        matches = await store.query(vector, scope, top_k)
        if matches:
            break
        await asyncio.sleep(1)
    return [m.id for m in matches]


@pytest.fixture
async def tenant(seeder: Seeder) -> TenantId:
    return await seeder.tenant()


async def test_best_match_comes_first(store: VectorStore, tenant: TenantId, seeder: Seeder) -> None:
    hr = await seeder.department(tenant)
    version = VersionId(uuid4())
    await store.upsert(
        [
            await _record(tenant, hr, version, 0, "annual leave is twenty five days"),
            await _record(tenant, hr, version, 1, "database failover runbook for kubernetes"),
        ]
    )
    ids = await _query(
        store, "how many days of annual leave", RetrievalScope(tenant, frozenset({hr}))
    )
    assert ids[0] == f"{version}:0"


async def test_only_the_scoped_departments_are_searched(
    store: VectorStore, tenant: TenantId, seeder: Seeder
) -> None:
    hr, eng = await seeder.department(tenant), await seeder.department(tenant)
    hr_version, eng_version = VersionId(uuid4()), VersionId(uuid4())
    await store.upsert(
        [
            await _record(tenant, hr, hr_version, 0, "salary bands for engineers"),
            await _record(tenant, eng, eng_version, 0, "salary bands for engineers"),
        ]
    )
    ids = await _query(store, "salary bands", RetrievalScope(tenant, frozenset({eng})))
    assert ids == [f"{eng_version}:0"]


async def test_another_tenant_is_never_searched(store: VectorStore, seeder: Seeder) -> None:
    acme, globex = await seeder.tenant(), await seeder.tenant()
    acme_hr, globex_hr = await seeder.department(acme), await seeder.department(globex)
    acme_version, globex_version = VersionId(uuid4()), VersionId(uuid4())
    await store.upsert(
        [
            await _record(acme, acme_hr, acme_version, 0, "merger plans for next quarter"),
            await _record(globex, globex_hr, globex_version, 0, "merger plans for next quarter"),
        ]
    )
    # Even naming the other tenant's department inside Acme's scope finds nothing of Globex.
    scope = RetrievalScope(acme, frozenset({acme_hr, globex_hr}))
    assert await _query(store, "merger plans", scope) == [f"{acme_version}:0"]


async def test_deactivated_versions_are_not_returned(
    store: VectorStore, tenant: TenantId, seeder: Seeder
) -> None:
    hr = await seeder.department(tenant)
    old, new = VersionId(uuid4()), VersionId(uuid4())
    await store.upsert(
        [
            await _record(tenant, hr, old, 0, "remote work is two days a week"),
            await _record(tenant, hr, new, 0, "remote work is three days a week"),
        ]
    )
    await store.deactivate_version(tenant, old)
    ids = await _query(store, "remote work days", RetrievalScope(tenant, frozenset({hr})))
    assert ids == [f"{new}:0"]


async def test_upsert_is_idempotent(store: VectorStore, tenant: TenantId, seeder: Seeder) -> None:
    hr = await seeder.department(tenant)
    version = VersionId(uuid4())
    record = await _record(tenant, hr, version, 0, "the same chunk indexed twice")
    await store.upsert([record])
    await store.upsert([record])
    ids = await _query(store, "the same chunk", RetrievalScope(tenant, frozenset({hr})), top_k=10)
    assert ids == [record.id]


async def test_an_empty_scope_returns_nothing(store: VectorStore, tenant: TenantId) -> None:
    assert (
        await store.query(await EMBEDDER.embed_query("x"), RetrievalScope(tenant, frozenset()), 5)
        == []
    )
