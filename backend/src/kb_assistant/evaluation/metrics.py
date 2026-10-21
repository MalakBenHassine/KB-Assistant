"""Quality metrics, as pure functions of what one run of the workflow produced.

Definitions (docs/EVALUATION.md has the reasoning):
- hit@k, MRR: over answerable questions, on the *documents* of the retrieved
  chunks in score order, duplicates removed; a document counts once however
  many of its chunks were retrieved.
- answer accuracy: an answerable question answered with every expected fact
  and no fact from somewhere it should not be.
- citation precision: cited documents that are expected documents (strict:
  a citation to another document that happens to agree still counts against).
- faithfulness: answer sentences whose content words are found in the sources
  they cite (a lexical check; it cannot see a negation or a paraphrase).
- abstention: whether the assistant answered, abstained or replied, as the
  category expects.
- leaks: retrieved chunks from a document the asker may not read. Must be 0.
"""

import re
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field

from kb_assistant.evaluation.dataset import Behaviour, Category

SUPPORTED_SENTENCE_MIN_OVERLAP = 0.8
HIT_AT = (1, 3, 5)

_MARKER = re.compile(r"\[S\d{1,3}\]")
_WORD = re.compile(r"[a-z0-9][a-z0-9%:.\-]*[a-z0-9%]|[a-z0-9]", re.IGNORECASE)
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "has",
        "have",
        "how",
        "i",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "our",
        "that",
        "the",
        "their",
        "them",
        "they",
        "this",
        "to",
        "was",
        "we",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "you",
        "your",
    ]
)


@dataclass(frozen=True, slots=True)
class QuestionResult:
    """What one question produced, reduced to what the metrics need."""

    id: str
    category: Category
    expected: Behaviour
    behaviour: Behaviour
    answer: str
    abstain_reason: str | None
    retrieved_documents: tuple[str, ...]  # corpus paths, best first, deduplicated
    top_score: float | None
    cited_documents: tuple[str, ...]
    sentence_support: tuple[float, ...]
    missing_facts: tuple[str, ...]
    forbidden_facts: tuple[str, ...]
    leaked_chunks: int
    latency_ms: float
    expected_documents: tuple[str, ...] = ()

    @property
    def correct_behaviour(self) -> bool:
        return self.behaviour is self.expected

    @property
    def correct(self) -> bool:
        if not self.correct_behaviour or self.forbidden_facts or self.leaked_chunks:
            return False
        return self.expected is not Behaviour.ANSWER or not self.missing_facts


def content_words(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(_MARKER.sub(" ", text)) if w.lower() not in _STOPWORDS}


def sentence_support(sentence: str, sources: Sequence[str]) -> float:
    """Share of the sentence's content words found in the sources it cites."""
    words = content_words(sentence)
    if not words:
        return 1.0
    available: set[str] = set().union(*(content_words(s) for s in sources)) if sources else set()
    return len(words & available) / len(words)


def facts_in(answer: str, facts: Sequence[str]) -> tuple[str, ...]:
    lowered = answer.lower()
    return tuple(f for f in facts if f.lower() in lowered)


def dedupe(items: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))


def first_relevant_rank(retrieved: Sequence[str], expected: Sequence[str]) -> int | None:
    for rank, document in enumerate(retrieved, start=1):
        if document in expected:
            return rank
    return None


def hit_at(k: int, retrieved: Sequence[str], expected: Sequence[str]) -> bool:
    rank = first_relevant_rank(retrieved, expected)
    return rank is not None and rank <= k


def reciprocal_rank(retrieved: Sequence[str], expected: Sequence[str]) -> float:
    rank = first_relevant_rank(retrieved, expected)
    return 0.0 if rank is None else 1.0 / rank


def citation_precision(cited: Sequence[str], expected: Sequence[str]) -> float | None:
    if not cited:
        return None
    return sum(1 for c in cited if c in expected) / len(cited)


def _mean(values: Sequence[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _rate(flags: Sequence[bool]) -> float:
    return sum(flags) / len(flags) if flags else 0.0


def _percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


@dataclass(frozen=True, slots=True)
class Summary:
    questions: int
    by_category: dict[str, int]
    hit_at_k: dict[int, float]
    mrr: float
    answer_accuracy: float
    citation_precision: float
    faithfulness: float
    abstention_accuracy: float
    abstention_recall: float  # should-abstain questions that abstained
    false_abstention_rate: float  # answerable questions that abstained
    leaked_chunks: int
    forbidden_facts: int
    latency_p50_ms: float
    latency_p95_ms: float
    failures: tuple[str, ...] = field(default=())

    def as_dict(self) -> dict[str, float | int]:
        """The flat metric names the gate and the reports use."""
        flat: dict[str, float | int] = {
            "questions": self.questions,
            "mrr": self.mrr,
            "answer_accuracy": self.answer_accuracy,
            "citation_precision": self.citation_precision,
            "faithfulness": self.faithfulness,
            "abstention_accuracy": self.abstention_accuracy,
            "abstention_recall": self.abstention_recall,
            "false_abstention_rate": self.false_abstention_rate,
            "leaked_chunks": self.leaked_chunks,
            "forbidden_facts": self.forbidden_facts,
            "latency_p50_ms": self.latency_p50_ms,
            "latency_p95_ms": self.latency_p95_ms,
        }
        flat.update({f"hit_at_{k}": v for k, v in self.hit_at_k.items()})
        return flat


def summarize(results: Sequence[QuestionResult]) -> Summary:
    answerable = [r for r in results if r.category is Category.ANSWERABLE]
    should_abstain = [r for r in results if r.expected is Behaviour.ABSTAIN]
    answered = [r for r in answerable if r.behaviour is Behaviour.ANSWER]
    precisions = [
        p
        for r in answered
        if (p := citation_precision(r.cited_documents, r.expected_documents)) is not None
    ]
    sentences = [s for r in results if r.behaviour is Behaviour.ANSWER for s in r.sentence_support]
    latencies = [r.latency_ms for r in results]
    categories: dict[str, int] = {}
    for r in results:
        categories[r.category.value] = categories.get(r.category.value, 0) + 1
    return Summary(
        questions=len(results),
        by_category=categories,
        hit_at_k={
            k: _rate([hit_at(k, r.retrieved_documents, r.expected_documents) for r in answerable])
            for k in HIT_AT
        },
        mrr=_mean(
            [reciprocal_rank(r.retrieved_documents, r.expected_documents) for r in answerable]
        ),
        answer_accuracy=_rate([r.correct for r in answerable]),
        citation_precision=_mean(precisions),
        faithfulness=_rate([s >= SUPPORTED_SENTENCE_MIN_OVERLAP for s in sentences]),
        abstention_accuracy=_rate([r.correct_behaviour for r in results]),
        abstention_recall=_rate([r.behaviour is Behaviour.ABSTAIN for r in should_abstain]),
        false_abstention_rate=_rate([r.behaviour is Behaviour.ABSTAIN for r in answerable]),
        leaked_chunks=sum(r.leaked_chunks for r in results),
        forbidden_facts=sum(len(r.forbidden_facts) for r in results),
        latency_p50_ms=_percentile(latencies, 0.5),
        latency_p95_ms=_percentile(latencies, 0.95),
        failures=tuple(r.id for r in results if not r.correct),
    )
