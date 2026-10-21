"""OpenTelemetry tracing (ARCHITECTURE §10).

Off unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set; the standard `OTEL_*`
variables configure the exporter. When on: one span per request (FastAPI), per
SQL statement (SQLAlchemy), per Redis command, per Celery task, and one per
node of the answering graph, so a slow answer shows which step was slow.

Span attributes carry identifiers and sizes, never question or document text
(SECURITY.md T5).
"""

import os
from contextlib import AbstractContextManager
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from sqlalchemy.ext.asyncio import AsyncEngine

# Probes and scrapes would drown the useful traces.
EXCLUDED_URLS = "healthz,readyz,metrics"


def tracing_enabled() -> bool:
    return bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))


def configure_tracing(service_name: str, engine: AsyncEngine | None = None) -> bool:
    """Install the exporter and the library instrumentations; False when tracing is off."""
    if not tracing_enabled():
        return False
    provider = TracerProvider(
        resource=Resource.create(
            {"service.name": os.environ.get("OTEL_SERVICE_NAME", service_name)}
        )
    )
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    RedisInstrumentor().instrument()
    if engine is not None:
        SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
    return True


def instrument_app(app: Any) -> None:
    if tracing_enabled():
        FastAPIInstrumentor.instrument_app(app, excluded_urls=EXCLUDED_URLS)


class OtelNodeTracer:
    """A span per graph node. A no-op when no tracer provider is installed."""

    def __init__(self) -> None:
        self._tracer = trace.get_tracer("kb_assistant.answering")

    def span(self, name: str) -> AbstractContextManager[Any]:
        return self._tracer.start_as_current_span(f"graph.{name}")


def current_trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None
