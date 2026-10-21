"""Choosing `min_score` from data instead of guessing it (ARCHITECTURE §5.7).

The probe pass runs every question with the threshold disabled and records,
per question, the best retrieval score and whether the rest of the workflow
produced a cited answer. A threshold `t` then predicts:

    answered(t) = top_score >= t and answered_without_threshold

which is exact when nothing before generation depends on the threshold (the
offline profile: the rewrite is the question itself, so a retry retrieves the
same scores) and an approximation otherwise (a hosted model rewrites the query
on retry). The gate pass that follows runs the real configuration, so the
published metrics never rely on this approximation.

The chosen value is the middle of the widest interval of best accuracy: a
threshold sitting on the edge of that interval flips on the next document.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from kb_assistant.evaluation.dataset import Behaviour

GRID_STEP = 0.01
GRID = tuple(round(i * GRID_STEP, 2) for i in range(round(1 / GRID_STEP) + 1))


@dataclass(frozen=True, slots=True)
class Probe:
    id: str
    expected: Behaviour  # only ANSWER and ABSTAIN questions are calibrated
    top_score: float | None
    answered_without_threshold: bool


@dataclass(frozen=True, slots=True)
class CurvePoint:
    threshold: float
    accuracy: float
    answer_rate: float  # answerable questions answered
    false_answer_rate: float  # should-abstain questions answered


@dataclass(frozen=True, slots=True)
class Calibration:
    recommended: float
    best_accuracy: float
    plateau: tuple[float, float]  # every threshold in it reaches best_accuracy
    curve: tuple[CurvePoint, ...]

    def contains(self, threshold: float) -> bool:
        low, high = self.plateau
        return low <= threshold <= high


def _answers(probe: Probe, threshold: float) -> bool:
    return (
        probe.answered_without_threshold
        and probe.top_score is not None
        and probe.top_score >= threshold
    )


def point(probes: Sequence[Probe], threshold: float) -> CurvePoint:
    answerable = [p for p in probes if p.expected is Behaviour.ANSWER]
    abstain = [p for p in probes if p.expected is Behaviour.ABSTAIN]
    correct = sum(_answers(p, threshold) for p in answerable) + sum(
        not _answers(p, threshold) for p in abstain
    )
    total = len(answerable) + len(abstain)
    return CurvePoint(
        threshold=threshold,
        accuracy=correct / total if total else 0.0,
        answer_rate=sum(_answers(p, threshold) for p in answerable) / len(answerable)
        if answerable
        else 0.0,
        false_answer_rate=sum(_answers(p, threshold) for p in abstain) / len(abstain)
        if abstain
        else 0.0,
    )


def calibrate(probes: Sequence[Probe]) -> Calibration:
    relevant = [p for p in probes if p.expected in (Behaviour.ANSWER, Behaviour.ABSTAIN)]
    if not relevant:
        raise ValueError("nothing to calibrate on")
    curve = tuple(point(relevant, t) for t in GRID)
    best = max(c.accuracy for c in curve)
    # Widest run of consecutive grid points at the best accuracy; first one wins a tie.
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for i, c in enumerate(curve):
        if c.accuracy == best and start is None:
            start = i
        if c.accuracy != best and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(curve) - 1))
    low, high = max(runs, key=lambda r: r[1] - r[0])
    return Calibration(
        recommended=round((GRID[low] + GRID[high]) / 2, 2),
        best_accuracy=best,
        plateau=(GRID[low], GRID[high]),
        curve=curve,
    )
