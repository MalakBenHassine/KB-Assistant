"""The evaluation's own logic: dataset checks, metrics, calibration, gate, report.

The evaluation decides whether a change may merge, so its arithmetic is
tested like any other business rule.
"""

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from kb_assistant.evaluation.calibration import GRID, Probe, calibrate
from kb_assistant.evaluation.dataset import (
    Behaviour,
    Category,
    corpus_documents,
    load_dataset,
)
from kb_assistant.evaluation.gate import Limits, check, load_limits
from kb_assistant.evaluation.metrics import (
    QuestionResult,
    citation_precision,
    content_words,
    facts_in,
    hit_at,
    reciprocal_rank,
    sentence_support,
    summarize,
)
from kb_assistant.evaluation.report import Report, to_markdown, write

EVAL_DIR = Path(__file__).resolve().parents[3] / "eval"
MINIMUM_QUESTIONS = 50


def result(
    id: str = "q",
    category: Category = Category.ANSWERABLE,
    behaviour: Behaviour = Behaviour.ANSWER,
    **overrides: object,
) -> QuestionResult:
    values: dict[str, object] = {
        "id": id,
        "category": category,
        "expected": {
            Category.ANSWERABLE: Behaviour.ANSWER,
            Category.CHITCHAT: Behaviour.REPLY,
        }.get(category, Behaviour.ABSTAIN),
        "behaviour": behaviour,
        "answer": "",
        "abstain_reason": None,
        "retrieved_documents": ("a.md",),
        "top_score": 0.5,
        "cited_documents": ("a.md",) if behaviour is Behaviour.ANSWER else (),
        "sentence_support": (1.0,) if behaviour is Behaviour.ANSWER else (),
        "missing_facts": (),
        "forbidden_facts": (),
        "leaked_chunks": 0,
        "latency_ms": 10.0,
        "expected_documents": ("a.md",) if category is Category.ANSWERABLE else (),
    }
    values.update(overrides)
    return QuestionResult(**values)  # type: ignore[arg-type]


# ----------------------------------------------------------------------------- dataset


def test_the_committed_dataset_is_valid_and_large_enough() -> None:
    questions = load_dataset(EVAL_DIR / "dataset.jsonl", EVAL_DIR / "corpus")
    assert len(questions) >= MINIMUM_QUESTIONS
    assert {q.category for q in questions} == set(Category)


def test_the_committed_corpus_covers_two_tenants_and_three_departments() -> None:
    documents = corpus_documents(EVAL_DIR / "corpus")
    assert len(documents) >= 30
    assert {d.split("/")[0] for d in documents} == {"acme", "globex"}
    assert {d.split("/")[1] for d in documents} == {"engineering", "hr", "product"}


def _write_dataset(tmp_path: Path, *rows: Mapping[str, object]) -> Path:
    path = tmp_path / "dataset.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return path


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    (tmp_path / "corpus" / "acme" / "hr").mkdir(parents=True)
    (tmp_path / "corpus" / "acme" / "hr" / "leave.md").write_text("# Leave", encoding="utf-8")
    return tmp_path / "corpus"


ANSWERABLE: dict[str, object] = {
    "id": "q1",
    "category": "answerable",
    "tenant": "acme",
    "departments": ["hr"],
    "question": "Leave?",
    "expected_documents": ["acme/hr/leave.md"],
    "must_include": ["25 days"],
}


def test_a_valid_question_loads(tmp_path: Path, corpus: Path) -> None:
    (question,) = load_dataset(_write_dataset(tmp_path, ANSWERABLE), corpus)
    assert question.expected is Behaviour.ANSWER


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"expected_documents": ["acme/hr/missing.md"]}, "not in the corpus"),
        ({"departments": ["engineering"]}, "cannot read"),
        ({"tenant": "globex"}, "cannot read"),
        ({"must_include": []}, "needs documents and facts"),
        ({"category": "restricted"}, "only answerable"),
        ({"role": "tenant_admin"}, "list none"),
        ({"surprise": 1}, "Extra inputs"),
    ],
)
def test_an_inconsistent_question_is_rejected_with_its_line(
    tmp_path: Path, corpus: Path, change: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        load_dataset(_write_dataset(tmp_path, {**ANSWERABLE, **change}), corpus)


def test_duplicate_ids_are_rejected(tmp_path: Path, corpus: Path) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        load_dataset(_write_dataset(tmp_path, ANSWERABLE, ANSWERABLE), corpus)


# ----------------------------------------------------------------------------- metrics


def test_ranking_metrics_use_the_first_expected_document() -> None:
    retrieved = ["x.md", "a.md", "b.md"]
    assert not hit_at(1, retrieved, ["a.md"])
    assert hit_at(3, retrieved, ["a.md", "b.md"])
    assert reciprocal_rank(retrieved, ["b.md", "a.md"]) == 0.5
    assert reciprocal_rank(retrieved, ["z.md"]) == 0.0


def test_citation_precision_is_strict_and_undefined_without_citations() -> None:
    assert citation_precision(["a.md", "x.md"], ["a.md"]) == 0.5
    assert citation_precision([], ["a.md"]) is None


def test_sentence_support_ignores_markers_and_stopwords() -> None:
    source = "Employees get 25 days of paid leave per year."
    assert sentence_support("Employees get 25 days of paid leave [S1].", [source]) == 1.0
    assert sentence_support("Employees get 30 days [S1].", [source]) == pytest.approx(3 / 4)
    assert sentence_support("The [S1].", [source]) == 1.0
    assert sentence_support("Anything [S1].", []) == 0.0


def test_content_words_keep_numbers_units_and_times() -> None:
    assert content_words("At 10:00, 5% of a 14-day trial costs 40 EUR.") == {
        "10:00",
        "5%",
        "14-day",
        "trial",
        "costs",
        "40",
        "eur",
    }


def test_facts_match_case_insensitively() -> None:
    assert facts_in("Twice a year, in June.", ["twice a year", "January"]) == ("twice a year",)


def test_a_correct_answer_needs_every_fact_no_forbidden_one_and_no_leak() -> None:
    assert result().correct
    assert not result(missing_facts=("25 days",)).correct
    assert not result(forbidden_facts=("22 days",)).correct
    assert not result(leaked_chunks=1).correct
    assert not result(behaviour=Behaviour.ABSTAIN).correct
    # Facts are only checked on answers: a correct abstention has none to state.
    assert result(category=Category.RESTRICTED, behaviour=Behaviour.ABSTAIN).correct


def test_summary_counts_each_metric_on_its_own_population() -> None:
    summary = summarize(
        [
            result("a1"),
            result("a2", behaviour=Behaviour.ABSTAIN, retrieved_documents=("x.md", "a.md")),
            result("r1", Category.RESTRICTED, Behaviour.ABSTAIN),
            result("u1", Category.UNANSWERABLE, Behaviour.ANSWER, sentence_support=(0.5,)),
            result("c1", Category.CHITCHAT, Behaviour.REPLY, retrieved_documents=()),
        ]
    )
    assert summary.questions == 5
    assert summary.hit_at_k[1] == 0.5  # over the two answerable questions only
    assert summary.mrr == 0.75
    assert summary.answer_accuracy == 0.5
    assert summary.false_abstention_rate == 0.5
    assert summary.abstention_recall == 0.5  # r1 abstained, u1 did not
    assert summary.abstention_accuracy == 3 / 5
    assert summary.faithfulness == 0.5  # one supported sentence of two
    assert summary.failures == ("a2", "u1")
    assert summary.as_dict()["hit_at_5"] == 1.0


# ----------------------------------------------------------------------------- calibration


def probe(expected: Behaviour, score: float | None, answered: bool = True) -> Probe:
    return Probe(id="p", expected=expected, top_score=score, answered_without_threshold=answered)


def test_calibration_picks_the_middle_of_the_best_interval() -> None:
    calibration = calibrate(
        [
            probe(Behaviour.ANSWER, 0.62),
            probe(Behaviour.ANSWER, 0.55),
            probe(Behaviour.ABSTAIN, 0.31),
            probe(Behaviour.ABSTAIN, 0.12),
            probe(Behaviour.ABSTAIN, None),
        ]
    )
    assert calibration.best_accuracy == 1.0
    assert calibration.plateau == (0.32, 0.55)
    assert calibration.recommended == pytest.approx(0.44, abs=0.01)
    assert calibration.contains(0.4)
    assert not calibration.contains(0.6)


def test_a_question_the_workflow_cannot_answer_is_never_counted_as_answered() -> None:
    calibration = calibrate(
        [probe(Behaviour.ANSWER, 0.9, answered=False), probe(Behaviour.ABSTAIN, 0.1)]
    )
    assert calibration.best_accuracy == 0.5
    assert all(p.answer_rate == 0.0 for p in calibration.curve)


def test_calibration_ignores_chitchat_and_needs_data() -> None:
    with pytest.raises(ValueError, match="nothing to calibrate"):
        calibrate([probe(Behaviour.REPLY, None)])
    assert len(calibrate([probe(Behaviour.ANSWER, 0.5)]).curve) == len(GRID)


# ----------------------------------------------------------------------------- gate


def test_the_gate_reports_every_violated_limit() -> None:
    limits = Limits(minimum={"mrr": 0.9}, maximum={"leaked_chunks": 0})
    assert check({"mrr": 0.95, "leaked_chunks": 0}, limits) == []
    failures = check({"mrr": 0.5, "leaked_chunks": 2}, limits)
    assert len(failures) == 2
    assert "mrr" in failures[0]
    assert "leaked_chunks" in failures[1]


def test_a_limit_on_an_unknown_metric_is_an_error_not_a_pass() -> None:
    with pytest.raises(ValueError, match="unknown metrics"):
        check({"mrr": 1.0}, Limits(minimum={"mmr": 0.9}, maximum={}))


def test_the_committed_gate_names_only_real_metrics_and_forbids_leaks() -> None:
    metrics = summarize([result()]).as_dict()
    for profile in ("offline", "openai"):
        limits = load_limits(EVAL_DIR / "gate.toml", profile)
        check(metrics, limits)  # raises on an unknown metric
        assert limits.maximum["leaked_chunks"] == 0
        assert limits.maximum["forbidden_facts"] == 0
    with pytest.raises(ValueError, match="no limits"):
        load_limits(EVAL_DIR / "gate.toml", "nonexistent")


# ----------------------------------------------------------------------------- report


def test_the_report_lists_failures_and_states_whether_the_threshold_is_calibrated(
    tmp_path: Path,
) -> None:
    results = [result(), result("bad", missing_facts=("25 days",))]
    report = Report(
        profile="offline",
        models={"embedder": "e", "chat": "c"},
        thresholds={"min_score": 0.9},
        summary=summarize(results),
        calibration=calibrate([probe(Behaviour.ANSWER, 0.5)]),
        gate_failures=["mrr too low"],
        results=results,
    )
    markdown = to_markdown(report)
    assert "**Gate: FAILED**" in markdown
    assert "missing 25 days" in markdown
    assert "outside the best interval" in markdown
    json_path, md_path = write(report, tmp_path / "reports")
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved["gate"]["passed"] is False
    assert saved["calibration"]["configured_is_calibrated"] is False
    assert md_path.read_text(encoding="utf-8") == markdown
