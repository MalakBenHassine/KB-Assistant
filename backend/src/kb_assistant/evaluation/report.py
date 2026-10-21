"""Reports: JSON for machines and history, Markdown for people and CI summaries."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from kb_assistant.evaluation.calibration import Calibration
from kb_assistant.evaluation.metrics import QuestionResult, Summary

CURVE_REPORT_STEP = 0.05


@dataclass(frozen=True, slots=True)
class Report:
    profile: str
    models: Mapping[str, str]  # embedder, chat model, prompt version, chunker
    thresholds: Mapping[str, Any]  # the configuration the gate pass ran with
    summary: Summary
    calibration: Calibration
    gate_failures: Sequence[str]
    results: Sequence[QuestionResult]

    def to_json(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "models": dict(self.models),
            "thresholds": dict(self.thresholds),
            "metrics": self.summary.as_dict(),
            "by_category": self.summary.by_category,
            "calibration": {
                "recommended_min_score": self.calibration.recommended,
                "plateau": list(self.calibration.plateau),
                "best_accuracy": self.calibration.best_accuracy,
                "configured_is_calibrated": self.calibration.contains(
                    float(self.thresholds["min_score"])
                ),
            },
            "gate": {"passed": not self.gate_failures, "failures": list(self.gate_failures)},
            "results": [
                {
                    **asdict(r),
                    "category": r.category.value,
                    "expected": r.expected.value,
                    "behaviour": r.behaviour.value,
                    "correct": r.correct,
                }
                for r in self.results
            ],
        }


def _pct(value: float) -> str:
    return f"{value * 100:.1f} %"


def to_markdown(report: Report) -> str:
    s = report.summary
    m = s.as_dict()
    cal = report.calibration
    configured = float(report.thresholds["min_score"])
    lines = [
        f"## Evaluation — profile `{report.profile}`",
        "",
        "**Gate: passed**" if not report.gate_failures else "**Gate: FAILED**",
        *(f"- {f}" for f in report.gate_failures),
        "",
        "| Models | |",
        "|---|---|",
        *(f"| {k} | `{v}` |" for k, v in report.models.items()),
        "",
        f"{s.questions} questions: "
        + ", ".join(f"{n} {c.replace('_', ' ')}" for c, n in sorted(s.by_category.items())),
        "",
        "| Metric | Value |",
        "|---|---|",
        *(f"| hit@{k} | {_pct(v)} |" for k, v in s.hit_at_k.items()),
        f"| MRR | {m['mrr']:.3f} |",
        f"| Answer accuracy | {_pct(s.answer_accuracy)} |",
        f"| Citation precision | {_pct(s.citation_precision)} |",
        f"| Faithfulness (lexical) | {_pct(s.faithfulness)} |",
        f"| Abstention accuracy | {_pct(s.abstention_accuracy)} |",
        f"| Abstention recall | {_pct(s.abstention_recall)} |",
        f"| False abstention rate | {_pct(s.false_abstention_rate)} |",
        f"| Leaked chunks | {s.leaked_chunks} |",
        f"| Facts from the wrong source | {s.forbidden_facts} |",
        f"| Latency p50 / p95 | {s.latency_p50_ms:.0f} ms / {s.latency_p95_ms:.0f} ms |",
        "",
        "### Threshold calibration",
        "",
        f"Configured `min_score` = {configured}; recommended {cal.recommended} "
        f"(best accuracy {_pct(cal.best_accuracy)} for every value in "
        f"[{cal.plateau[0]}, {cal.plateau[1]}]). "
        + (
            "The configured value is calibrated."
            if cal.contains(configured)
            else "**The configured value is outside the best interval.**"
        ),
        "",
        "| min_score | Accuracy | Answer rate | False answer rate |",
        "|---|---|---|---|",
    ]
    step = round(CURVE_REPORT_STEP / (cal.curve[1].threshold - cal.curve[0].threshold))
    for p in cal.curve[::step]:
        lines.append(
            f"| {p.threshold:.2f} | {_pct(p.accuracy)} | {_pct(p.answer_rate)} "
            f"| {_pct(p.false_answer_rate)} |"
        )
    wrong = [r for r in report.results if not r.correct]
    lines += ["", f"### Incorrect replies ({len(wrong)})", ""]
    if wrong:
        lines += ["| Question | Expected | Got | Why |", "|---|---|---|---|"]
        for r in wrong:
            why = []
            if r.missing_facts and r.correct_behaviour:
                why.append("missing " + ", ".join(r.missing_facts))
            if r.forbidden_facts:
                why.append("states " + ", ".join(r.forbidden_facts))
            if r.leaked_chunks:
                why.append(f"{r.leaked_chunks} leaked chunks")
            if r.abstain_reason and not r.correct_behaviour:
                why.append(r.abstain_reason)
            lines.append(
                f"| `{r.id}` | {r.expected.value} | {r.behaviour.value} | {'; '.join(why)} |"
            )
    return "\n".join(lines) + "\n"


def write(report: Report, directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{report.profile}.json"
    md_path = directory / f"{report.profile}.md"
    json_path.write_text(json.dumps(report.to_json(), indent=2) + "\n", encoding="utf-8")
    md_path.write_text(to_markdown(report), encoding="utf-8")
    return json_path, md_path
