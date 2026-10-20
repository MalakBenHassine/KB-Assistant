"""What the Pinecone adapter sends, asserted on the outgoing call (SECURITY.md, T1)."""

from typing import Any
from uuid import uuid4

from kb_assistant.domain.documents import RetrievalScope, VectorRecord
from kb_assistant.domain.ids import DepartmentId, DocumentId, TenantId, VersionId
from kb_assistant.infrastructure.vector.pinecone import PineconeVectorStore
from tests.fakes import FakePineconeIndex

TENANT = TenantId(uuid4())
HR, ENG = DepartmentId(uuid4()), DepartmentId(uuid4())


def store(index: FakePineconeIndex) -> PineconeVectorStore:
    fake: Any = index
    return PineconeVectorStore(fake)


def record(page: int | None) -> VectorRecord:
    version = VersionId(uuid4())
    return VectorRecord(
        id=f"{version}:0",
        values=(0.1, 0.2),
        tenant_id=TENANT,
        department_id=HR,
        document_id=DocumentId(uuid4()),
        version_id=version,
        chunk_index=0,
        page=page,
    )


async def test_query_uses_the_tenant_namespace_and_the_scope_filter() -> None:
    index = FakePineconeIndex()
    await store(index).query([0.1, 0.2], RetrievalScope(TENANT, frozenset({HR, ENG})), top_k=5)
    ((name, kwargs),) = index.calls
    assert name == "query"
    assert kwargs["namespace"] == f"t_{TENANT}"
    assert kwargs["filter"] == {
        "$and": [
            {"department_id": {"$in": sorted([str(HR), str(ENG)])}},
            {"is_active": {"$eq": True}},
        ]
    }
    assert kwargs["top_k"] == 5


async def test_an_empty_scope_never_reaches_pinecone() -> None:
    index = FakePineconeIndex()
    assert await store(index).query([0.1], RetrievalScope(TENANT, frozenset()), top_k=5) == []
    assert index.calls == []


async def test_matches_from_another_tenant_are_dropped() -> None:
    index = FakePineconeIndex()
    index.add_match("mine", 0.9, str(TENANT))
    index.add_match("intruder", 0.95, str(uuid4()))
    matches = await store(index).query([0.1], RetrievalScope(TENANT, frozenset({HR})), top_k=5)
    assert [m.id for m in matches] == ["mine"]


async def test_upsert_metadata_holds_identifiers_only() -> None:
    index = FakePineconeIndex()
    await store(index).upsert([record(page=None), record(page=3)])
    ((name, kwargs),) = index.calls
    assert name == "upsert"
    assert kwargs["namespace"] == f"t_{TENANT}"
    first, second = (v["metadata"] for v in kwargs["vectors"])
    assert "page" not in first  # Pinecone rejects null metadata values
    assert second["page"] == 3
    assert set(second) == {
        "tenant_id",
        "department_id",
        "document_id",
        "version_id",
        "chunk_index",
        "page",
        "is_active",
    }


async def test_archiving_a_version_is_one_update_by_filter() -> None:
    index = FakePineconeIndex()
    version = VersionId(uuid4())
    await store(index).deactivate_version(TENANT, version)
    ((name, kwargs),) = index.calls
    assert name == "update"
    assert kwargs["filter"] == {"version_id": {"$eq": str(version)}}
    assert kwargs["set_metadata"] == {"is_active": False}
    assert kwargs["namespace"] == f"t_{TENANT}"
