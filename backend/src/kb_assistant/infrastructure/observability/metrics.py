"""Prometheus metrics (ARCHITECTURE §10): RED per route, and what the product does.

Labels are bounded on purpose: the route *template*, never the raw path; the
model id; an abstention reason from a fixed enum. No tenant or user label: a
per-tenant breakdown belongs in analytics (Postgres), not in a time series
whose cardinality grows with every customer.

Each telemetry object owns its registry, so tests do not share counters. In
the Celery worker, set `PROMETHEUS_MULTIPROC_DIR`: the forked children write
their samples to files and the parent process serves them (`serve_worker_metrics`).
"""

import os

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
    multiprocess,
    start_http_server,
)

HTTP_BUCKETS_S = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)
ANSWER_BUCKETS_S = (0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 15, 30, 60)
INGESTION_BUCKETS_S = (0.5, 1, 2, 5, 10, 30, 60, 120, 300)
UNMATCHED_ROUTE = "unmatched"


class PrometheusTelemetry:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        r = self.registry
        self._requests = Counter(
            "kb_http_requests", "HTTP requests", ["method", "route", "status"], registry=r
        )
        self._request_seconds = Histogram(
            "kb_http_request_duration_seconds",
            "HTTP request duration",
            ["method", "route"],
            buckets=HTTP_BUCKETS_S,
            registry=r,
        )
        self._answers = Counter(
            "kb_answers", "Answers by outcome and retrieval grade", ["outcome", "grade"], registry=r
        )
        self._abstentions = Counter(
            "kb_abstentions", "Abstentions by reason", ["reason"], registry=r
        )
        self._answer_seconds = Histogram(
            "kb_answer_duration_seconds",
            "Time from question to the last byte of the answer",
            buckets=ANSWER_BUCKETS_S,
            registry=r,
        )
        self._tokens = Counter(
            "kb_llm_tokens", "Language-model tokens", ["model", "kind"], registry=r
        )
        self._hydration = Counter(
            "kb_authz_hydration_rejections",
            "Vector hits refused by the Postgres re-check (archived versions, or a leak)",
            registry=r,
        )
        self._limited = Counter(
            "kb_rate_limited", "Questions refused by a rate limit or budget", ["kind"], registry=r
        )
        self._ingestions = Counter(
            "kb_ingestions", "Ingestion attempts by outcome", ["status"], registry=r
        )
        self._ingestion_seconds = Histogram(
            "kb_ingestion_duration_seconds",
            "Ingestion duration, claim to switch",
            buckets=INGESTION_BUCKETS_S,
            registry=r,
        )

    def request_finished(
        self, *, method: str, route: str | None, status: int, seconds: float
    ) -> None:
        template = route or UNMATCHED_ROUTE
        self._requests.labels(method, template, str(status)).inc()
        self._request_seconds.labels(method, template).observe(seconds)

    def answer_finished(
        self,
        *,
        answered: bool,
        grade: str | None,
        abstain_reason: str | None,
        model: str,
        input_tokens: int,
        output_tokens: int,
        hydration_rejections: int,
        seconds: float,
    ) -> None:
        self._answers.labels("answered" if answered else "abstained", grade or "none").inc()
        if abstain_reason:
            self._abstentions.labels(abstain_reason).inc()
        self._tokens.labels(model, "input").inc(input_tokens)
        self._tokens.labels(model, "output").inc(output_tokens)
        if hydration_rejections:
            self._hydration.inc(hydration_rejections)
        self._answer_seconds.observe(seconds)

    def rate_limited(self, kind: str) -> None:
        self._limited.labels(kind).inc()

    def ingestion_finished(self, *, status: str, seconds: float) -> None:
        self._ingestions.labels(status).inc()
        self._ingestion_seconds.observe(seconds)

    def exposition(self) -> tuple[bytes, str]:
        return generate_latest(self.registry), CONTENT_TYPE_LATEST


def serve_worker_metrics(port: int) -> bool:
    """In the Celery parent: serve the samples the forked children wrote to disk."""
    if not os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        return False
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
    start_http_server(port, registry=registry)
    return True
