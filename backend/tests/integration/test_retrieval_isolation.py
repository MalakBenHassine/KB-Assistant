"""Phase 3 acceptance: a user of department A NEVER receives chunks of department B
or of another tenant (SECURITY.md, T1).

Asserted where it matters, not on the answer text:
- on the arguments of the vector-store call (check 1 receives the right scope);
- on the prompt sent to the model, with a deliberately *leaky* vector store
  that ignores the scope and returns every vector of every tenant, so that
  hydration from Postgres (check 2) and row-level security (check 3) are the
  only things standing between the leak and the model.

Everything is real except the leak: Postgres with RLS, the ingestion pipeline,
pgvector, the LangGraph workflow, the offline extractive model.
"""

import itertools
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from kb_assistant.application.answering.graph import AnswerPolicy, GraphDeps, build_answer_graph
from kb_assistant.application.answering.service import (
    AskQuestion,
    CreateConversation,
    PreparedQuestion,
    hydrate,
)
from kb_assistant.application.documents import UploadDocument, UploadNewVersion, UploadResult
from kb_assistant.bootstrap import Container, WorkerContainer
from kb_assistant.domain.documents import RetrievalScope, VectorMatch, VectorRecord
from kb_assistant.domain.errors import ForbiddenError
from kb_assistant.domain.ids import DepartmentId, TenantId, VersionId
from kb_assistant.domain.models import Role
from kb_assistant.domain.principal import Principal
from kb_assistant.infrastructure.llm.extractive import ExtractiveChatModel
from kb_assistant.infrastructure.vector.pgvector import PgVectorStore
from tests.fakes import RecordingJobQueue
from tests.support import Seeder

HR_TEXT = "# Compensation\n\nSalary bands for senior engineers range from 90k to 120k EUR."
ENG_TEXT = "# Deployments\n\nProduction deployments happen every Tuesday after change review."
LEGAL_TEXT = "# Contracts\n\nVendor contracts above 50k EUR need a legal review and two signatures."
GLOBEX_TEXT = "# Compensation\n\nGlobex salary bands for senior engineers range from 70k to 80k."


@dataclass
class SpyStore:
    inner: PgVectorStore
    scopes: list[RetrievalScope] = field(default_factory=list)

    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        await self.inner.upsert(records)

    async def deactivate_version(self, tenant_id: TenantId, version_id: VersionId) -> None:
        await self.inner.deactivate_version(tenant_id, version_id)

    async def query(
        self, vector: Sequence[float], scope: RetrievalScope, top_k: int
    ) -> list[VectorMatch]:
        self.scopes.append(scope)
        return await self.inner.query(vector, scope, top_k)


@dataclass
class LeakyStore:
    """A broken vector store: ignores the scope and returns every vector it has."""

    owner_engine: AsyncEngine
    scopes: list[RetrievalScope] = field(default_factory=list)

    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        raise NotImplementedError

    async def deactivate_version(self, tenant_id: TenantId, version_id: VersionId) -> None:
        raise NotImplementedError

    async def query(
        self, vector: Sequence[float], scope: RetrievalScope, top_k: int
    ) -> list[VectorMatch]:
        self.scopes.append(scope)
        async with self.owner_engine.connect() as conn:  # owner: bypasses RLS
            ids = (await conn.execute(text("SELECT id FROM vector_records"))).scalars().all()
        return [VectorMatch(id=i, score=0.99) for i in ids]


class RecordingModel(ExtractiveChatModel):
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def stream(self, *, system: str, user: str, max_output_tokens: int) -> Any:
        self.prompts.append(user)
        async for item in super().stream(
            system=system, user=user, max_output_tokens=max_output_tokens
        ):
            yield item


@dataclass
class Corpus:
    acme: TenantId
    hr: DepartmentId
    eng: DepartmentId
    legal: DepartmentId
    globex: TenantId
    hr_document: UploadResult


async def _upload(
    container: Container,
    worker: WorkerContainer,
    admin: Principal,
    department: DepartmentId,
    body: str,
    name: str,
) -> UploadResult:
    async with container.unit_of_work() as uow:
        await uow.bind(tenant_id=admin.tenant_id, user_id=admin.user_id)
        result = await UploadDocument(
            uow, container.blobs, RecordingJobQueue(), container.clock, container.upload_policy
        ).execute(admin, department_id=department, title=None, filename=name, data=body.encode())
    await worker.ingest().execute(admin.tenant_id, result.version.id)
    return result


async def _admin(seeder: Seeder, tenant: TenantId) -> Principal:
    member = await seeder.member(tenant, "tenant_admin")
    return Principal(member.user_id, tenant, member.membership_id, Role.TENANT_ADMIN, frozenset())


@pytest.fixture(scope="module")
async def corpus(seeder: Seeder, container: Container, worker: WorkerContainer) -> Corpus:
    acme, globex = await seeder.tenant("Acme"), await seeder.tenant("Globex")
    hr = await seeder.department(acme, "hr")
    eng = await seeder.department(acme, "engineering")
    legal = await seeder.department(acme, "legal")
    globex_hr = await seeder.department(globex, "hr")
    acme_admin, globex_admin = await _admin(seeder, acme), await _admin(seeder, globex)
    hr_document = await _upload(container, worker, acme_admin, hr, HR_TEXT, "pay.md")
    await _upload(container, worker, acme_admin, eng, ENG_TEXT, "deploy.md")
    await _upload(container, worker, acme_admin, legal, LEGAL_TEXT, "contracts.md")
    await _upload(container, worker, globex_admin, globex_hr, GLOBEX_TEXT, "pay.md")
    return Corpus(acme, hr, eng, legal, globex, hr_document)


async def _employee(seeder: Seeder, tenant: TenantId, *departments: DepartmentId) -> Principal:
    member = await seeder.member(tenant, "employee", departments)
    return Principal(
        member.user_id, tenant, member.membership_id, Role.EMPLOYEE, frozenset(departments)
    )


async def _ask(
    container: Container,
    store: Any,
    principal: Principal,
    question: str,
    requested: frozenset[DepartmentId] | None = None,
) -> tuple[RecordingModel, dict[str, Any]]:
    model = RecordingModel()
    graph = build_answer_graph(
        GraphDeps(
            embedder=container.embedder,
            vectors=store,
            hydrate=lambda ids, scope: hydrate(container.unit_of_work, ids, scope),
            chat=model,
            reranker=None,
            policy=AnswerPolicy(min_score=0.0),
        )
    )
    use_case = AskQuestion(
        uow_factory=container.unit_of_work,
        graph=graph,
        policy=AnswerPolicy(min_score=0.0),
        clock=container.clock,
        model_id=model.model_id,
    )
    async with container.unit_of_work() as uow:
        await uow.bind(tenant_id=principal.tenant_id, user_id=principal.user_id)
        conversation = await CreateConversation(uow, container.clock).execute(principal, None)
    prepared: PreparedQuestion = await use_case.prepare(
        principal, conversation.id, question, requested, f"req-{uuid4().hex[:8]}"
    )
    done: dict[str, Any] = {}
    async for event in use_case.stream(prepared):
        if event.kind == "done":
            done = event.data
    return model, done


async def test_the_vector_store_is_queried_with_the_callers_scope_only(
    seeder: Seeder, container: Container, corpus: Corpus
) -> None:
    engineer = await _employee(seeder, corpus.acme, corpus.eng)
    store = SpyStore(PgVectorStore(container.engine))
    await _ask(container, store, engineer, "What are the salary bands for senior engineers?")
    assert store.scopes, "the vector store was never queried"
    for scope in store.scopes:
        assert scope == RetrievalScope(corpus.acme, frozenset({corpus.eng}))


async def test_a_leaking_index_still_cannot_put_foreign_text_in_the_prompt(
    seeder: Seeder, container: Container, owner_engine: AsyncEngine, corpus: Corpus
) -> None:
    engineer = await _employee(seeder, corpus.acme, corpus.eng)
    store = LeakyStore(owner_engine)
    model, done = await _ask(container, store, engineer, "What are the salary bands?")
    prompts = "\n".join(model.prompts)
    assert "Salary bands for senior engineers" not in prompts  # Acme HR
    assert "Globex" not in prompts  # another tenant
    assert "legal review" not in prompts  # Acme Legal
    assert "Salary" not in done["answer"]
    for citation in done["citations"]:
        assert citation["document_title"] == "deploy.md"


async def test_every_department_combination_only_ever_sees_its_own_chunks(
    seeder: Seeder, container: Container, owner_engine: AsyncEngine, corpus: Corpus
) -> None:
    # Phrases found only in each department's document, never in the question.
    texts = {corpus.hr: "90k to 120k", corpus.eng: "every Tuesday", corpus.legal: "two signatures"}
    for size in range(4):
        for departments in itertools.combinations(texts, size):
            person = await _employee(seeder, corpus.acme, *departments)
            model, _ = await _ask(
                container, LeakyStore(owner_engine), person, "salary deployments vendor contracts"
            )
            prompts = "\n".join(model.prompts)
            for department, marker in texts.items():
                if department not in departments:
                    assert marker not in prompts, (departments, marker)
            assert "Globex" not in prompts


async def test_asking_for_a_foreign_department_fails_before_any_search(
    seeder: Seeder, container: Container, corpus: Corpus
) -> None:
    engineer = await _employee(seeder, corpus.acme, corpus.eng)
    store = SpyStore(PgVectorStore(container.engine))
    with pytest.raises(ForbiddenError):
        await _ask(container, store, engineer, "salary bands", frozenset({corpus.hr}))
    assert store.scopes == []


async def test_archived_versions_are_never_hydrated(
    seeder: Seeder,
    container: Container,
    worker: WorkerContainer,
    owner_engine: AsyncEngine,
    corpus: Corpus,
) -> None:
    admin = await _admin(seeder, corpus.acme)
    async with container.unit_of_work() as uow:
        await uow.bind(tenant_id=admin.tenant_id, user_id=admin.user_id)
        updated = await UploadNewVersion(
            uow, container.blobs, RecordingJobQueue(), container.clock, container.upload_policy
        ).execute(
            admin,
            corpus.hr_document.document.id,
            filename="pay.md",
            data=b"# Compensation\n\nSalary bands were revised in October: 95k to 125k EUR.",
        )
    await worker.ingest().execute(admin.tenant_id, updated.version.id)

    hr_person = await _employee(seeder, corpus.acme, corpus.hr)
    model, _ = await _ask(container, LeakyStore(owner_engine), hr_person, "salary bands")
    prompts = "\n".join(model.prompts)
    assert "revised in October" in prompts
    assert "90k to 120k" not in prompts  # the archived version


async def test_the_question_and_the_retrieved_chunks_are_audited(
    seeder: Seeder, container: Container, owner_engine: AsyncEngine, corpus: Corpus
) -> None:
    engineer = await _employee(seeder, corpus.acme, corpus.eng)
    _, done = await _ask(
        container,
        SpyStore(PgVectorStore(container.engine)),
        engineer,
        "When do production deployments happen?",
    )
    async with owner_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT details FROM audit_log WHERE action = 'chat.question' AND target = :t"
                ),
                {"t": done["message_id"]},
            )
        ).one()
        message = (
            await conn.execute(
                text(
                    "SELECT model, prompt_version, thresholds, retrieved_chunk_ids, latency_ms"
                    " FROM messages WHERE id = :m"
                ),
                {"m": done["message_id"]},
            )
        ).one()
    assert row.details["question"] == "When do production deployments happen?"
    assert row.details["departments"] == [str(corpus.eng)]
    assert row.details["retrieved_chunk_ids"]
    assert message.model == "local-extractive-v1"
    assert message.prompt_version == "answer-v1"
    assert message.thresholds["min_score"] == 0.0
    assert message.latency_ms is not None
