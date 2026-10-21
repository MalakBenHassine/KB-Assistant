"""Metrics and tracing: what operators see, and what they must never see."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from prometheus_client import CollectorRegistry

from kb_assistant.application.answering.graph import GraphDeps, build_answer_graph
from kb_assistant.infrastructure.observability.logging import trace_processor
from kb_assistant.infrastructure.observability.metrics import (
    UNMATCHED_ROUTE,
    PrometheusTelemetry,
)
from tests.chat_fakes import ConstantEmbedder, FakeHydrator, ScriptedChatModel, SpyVectorStore
from tests.unit.test_answer_graph import LEAVE, POLICY, World


def sample(telemetry: PrometheusTelemetry, name: str, **labels: str) -> float | None:
    value: float | None = telemetry.registry.get_sample_value(name, labels)
    return value


def test_requests_are_counted_by_route_template_with_a_bounded_label_set() -> None:
    t = PrometheusTelemetry(CollectorRegistry())
    t.request_finished(method="GET", route="/v1/documents/{document_id}", status=200, seconds=0.02)
    t.request_finished(method="GET", route=None, status=404, seconds=0.001)
    assert (
        sample(
            t,
            "kb_http_requests_total",
            method="GET",
            route="/v1/documents/{document_id}",
            status="200",
        )
        == 1
    )
    assert (
        sample(t, "kb_http_requests_total", method="GET", route=UNMATCHED_ROUTE, status="404") == 1
    )


def test_answers_tokens_abstentions_and_leaks_are_counted() -> None:
    t = PrometheusTelemetry(CollectorRegistry())
    common: dict[str, Any] = {
        "model": "m",
        "input_tokens": 100,
        "output_tokens": 20,
        "seconds": 1.5,
    }
    t.answer_finished(
        answered=True, grade="good", abstain_reason=None, hydration_rejections=0, **common
    )
    t.answer_finished(
        answered=False,
        grade="weak",
        abstain_reason="weak_evidence",
        hydration_rejections=2,
        **common,
    )
    assert sample(t, "kb_answers_total", outcome="answered", grade="good") == 1
    assert sample(t, "kb_abstentions_total", reason="weak_evidence") == 1
    assert sample(t, "kb_llm_tokens_total", model="m", kind="input") == 200
    assert sample(t, "kb_authz_hydration_rejections_total") == 2
    assert sample(t, "kb_answer_duration_seconds_count") == 2


def test_the_exposition_has_no_tenant_or_user_label() -> None:
    t = PrometheusTelemetry(CollectorRegistry())
    t.rate_limited("user")
    t.ingestion_finished(status="indexed", seconds=3.0)
    body, media_type = t.exposition()
    text = body.decode()
    assert media_type.startswith("text/plain")
    assert 'kb_rate_limited_total{kind="user"} 1.0' in text
    assert 'kb_ingestions_total{status="indexed"} 1.0' in text
    assert "tenant" not in text
    assert "user_id" not in text


class RecordingTracer:
    def __init__(self) -> None:
        self.spans: list[str] = []

    @contextmanager
    def span(self, name: str) -> Iterator[None]:
        self.spans.append(name)
        yield


async def test_every_graph_node_runs_inside_its_own_span() -> None:
    tracer = RecordingTracer()
    world = World()
    world.graph = build_answer_graph(
        GraphDeps(
            embedder=ConstantEmbedder(),
            vectors=SpyVectorStore(matches=world.vectors.matches),
            hydrate=FakeHydrator({LEAVE.vector_id: LEAVE}),
            chat=ScriptedChatModel(answer="Annual leave is 25 days [S1]."),
            reranker=None,
            policy=POLICY,
            tracer=tracer,
        )
    )
    await world.ask()
    assert tracer.spans == [
        "classify_intent",
        "rewrite_query",
        "retrieve",
        "grade_retrieval",
        "rerank",
        "generate",
        "validate_citations",
    ]


def test_logs_carry_no_trace_id_when_tracing_is_off() -> None:
    event = trace_processor(None, "info", {"event": "x"})
    assert "trace_id" not in event


def test_registries_are_independent() -> None:
    first, second = (
        PrometheusTelemetry(CollectorRegistry()),
        PrometheusTelemetry(CollectorRegistry()),
    )
    first.rate_limited("budget")
    assert sample(second, "kb_rate_limited_total", kind="budget") is None
