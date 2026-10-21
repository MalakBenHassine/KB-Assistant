"""The offline evaluation and its gate (docs/EVALUATION.md).

Builds the two demo tenants, indexes the 30 documents of `eval/corpus` through
the real pipeline, then asks every question of `eval/dataset.jsonl` twice:
- a probe pass with the score threshold disabled, to calibrate `min_score`;
- a gate pass with the configuration as deployed, whose metrics are compared
  with `eval/gate.toml`.

Reports go to `KB_EVAL_REPORT_DIR` (`make eval` sets `eval/reports`), and to
the job summary when running in GitHub Actions.
"""

import os
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from kb_assistant.application.answering.prompts import PROMPT_VERSION
from kb_assistant.application.chunking import CHUNKER_VERSION
from kb_assistant.application.documents import UploadDocument
from kb_assistant.bootstrap import Container, WorkerContainer
from kb_assistant.domain.documents import VersionStatus
from kb_assistant.domain.ids import DepartmentId, DocumentId, TenantId
from kb_assistant.domain.models import Role
from kb_assistant.domain.principal import Principal
from kb_assistant.evaluation.calibration import calibrate
from kb_assistant.evaluation.dataset import EvalQuestion, corpus_documents, load_dataset
from kb_assistant.evaluation.gate import check, load_limits
from kb_assistant.evaluation.metrics import summarize
from kb_assistant.evaluation.report import Report, to_markdown, write
from kb_assistant.evaluation.runner import Asker, Evaluator, probe_of
from tests.conftest import BACKEND_DIR
from tests.fakes import RecordingJobQueue
from tests.support import Seeder

pytestmark = pytest.mark.eval

EVAL_DIR = BACKEND_DIR.parent / "eval"
CORPUS = EVAL_DIR / "corpus"
# Below any cosine similarity: in the probe pass, no retrieval is ever "weak".
PROBE_MIN_SCORE = -1.0


@dataclass
class IndexedCorpus:
    paths: dict[DocumentId, str]
    tenants: dict[str, TenantId]
    departments: dict[tuple[str, str], DepartmentId]


async def _admin(seeder: Seeder, tenant: TenantId) -> Principal:
    member = await seeder.member(tenant, "tenant_admin")
    return Principal(member.user_id, tenant, member.membership_id, Role.TENANT_ADMIN, frozenset())


@pytest.fixture(scope="module")
async def corpus(seeder: Seeder, container: Container, worker: WorkerContainer) -> IndexedCorpus:
    indexed = IndexedCorpus(paths={}, tenants={}, departments={})
    admins: dict[str, Principal] = {}
    for path in corpus_documents(CORPUS):
        tenant, department, filename = path.split("/")
        if tenant not in indexed.tenants:
            indexed.tenants[tenant] = await seeder.tenant(tenant.title())
            admins[tenant] = await _admin(seeder, indexed.tenants[tenant])
        if (tenant, department) not in indexed.departments:
            indexed.departments[(tenant, department)] = await seeder.department(
                indexed.tenants[tenant], department
            )
        admin = admins[tenant]
        async with container.unit_of_work() as uow:
            await uow.bind(tenant_id=admin.tenant_id, user_id=admin.user_id)
            uploaded = await UploadDocument(
                uow, container.blobs, RecordingJobQueue(), container.clock, container.upload_policy
            ).execute(
                admin,
                department_id=indexed.departments[(tenant, department)],
                title=None,
                filename=filename,
                data=(CORPUS / path).read_bytes(),
            )
        outcome = await worker.ingest().execute(admin.tenant_id, uploaded.version.id)
        assert outcome.status is VersionStatus.INDEXED, f"{path} was not indexed"
        indexed.paths[uploaded.document.id] = path
    return indexed


class Askers:
    """One member per distinct (tenant, role, departments), created on first use."""

    def __init__(self, seeder: Seeder, corpus: IndexedCorpus) -> None:
        self._seeder = seeder
        self._corpus = corpus
        self._cache: dict[tuple[str, str, tuple[str, ...]], Asker] = {}

    async def __call__(self, question: EvalQuestion) -> Asker:
        key = (question.tenant, question.role, tuple(sorted(question.departments)))
        if key not in self._cache:
            tenant_id = self._corpus.tenants[question.tenant]
            departments = tuple(
                self._corpus.departments[(question.tenant, d)] for d in question.departments
            )
            member = await self._seeder.member(tenant_id, question.role, departments)
            principal = Principal(
                member.user_id,
                tenant_id,
                member.membership_id,
                Role(question.role),
                frozenset(departments),
            )
            admin = question.role == "tenant_admin"
            readable = frozenset(
                path
                for path in self._corpus.paths.values()
                if path.split("/")[0] == question.tenant
                and (admin or path.split("/")[1] in question.departments)
            )
            self._cache[key] = Asker(principal, readable)
        return self._cache[key]


def _append(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(text)


def _report_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    configured = os.environ.get("KB_EVAL_REPORT_DIR")
    return Path(configured) if configured else tmp_path_factory.mktemp("eval-reports")


async def test_quality_gate(
    seeder: Seeder,
    container: Container,
    corpus: IndexedCorpus,
    profile: str,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    questions = load_dataset(EVAL_DIR / "dataset.jsonl", CORPUS)
    policy = container.answer_policy
    probe_policy = replace(policy, min_score=PROBE_MIN_SCORE)
    probe = Evaluator(
        graph=container.graph_for(probe_policy),
        policy=probe_policy,
        uow_factory=container.unit_of_work,
        documents=corpus.paths,
    )
    gate = Evaluator(
        graph=container.answer_graph,
        policy=policy,
        uow_factory=container.unit_of_work,
        documents=corpus.paths,
    )
    askers = Askers(seeder, corpus)

    probes, results = [], []
    for question in questions:
        asker = await askers(question)
        probes.append(probe_of(await probe.run(question, asker)))
        results.append(await gate.run(question, asker))

    summary = summarize(results)
    calibration = calibrate(probes)
    limits = load_limits(EVAL_DIR / "gate.toml", profile)
    failures = check(summary.as_dict(), limits)
    if limits.require_calibrated_threshold and not calibration.contains(policy.min_score):
        failures.append(
            f"min_score = {policy.min_score} is outside the calibrated interval "
            f"{list(calibration.plateau)}; recommended {calibration.recommended}"
        )
    report = Report(
        profile=profile,
        models={
            "embedder": container.embedder.model_id,
            "chat": container.chat.model_id,
            "prompt": PROMPT_VERSION,
            "chunker": CHUNKER_VERSION,
        },
        thresholds=policy.thresholds(),
        summary=summary,
        calibration=calibration,
        gate_failures=failures,
        results=results,
    )
    write(report, _report_dir(tmp_path_factory))
    if summary_file := os.environ.get("GITHUB_STEP_SUMMARY"):
        _append(Path(summary_file), to_markdown(report))

    assert not failures, "quality gate failed:\n" + "\n".join(failures)
