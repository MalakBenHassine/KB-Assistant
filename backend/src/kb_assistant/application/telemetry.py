"""Null implementations of the observability ports, for tests and tools.

The composition root wires the real ones (Prometheus, OpenTelemetry); use
cases default to these so that measuring is never a precondition for working.
"""

from contextlib import AbstractContextManager, nullcontext
from typing import Any


class NullTelemetry:
    def request_finished(
        self, *, method: str, route: str | None, status: int, seconds: float
    ) -> None:
        del method, route, status, seconds

    def answer_finished(self, **_: Any) -> None:
        return None

    def rate_limited(self, kind: str) -> None:
        del kind

    def ingestion_finished(self, *, status: str, seconds: float) -> None:
        del status, seconds

    def exposition(self) -> tuple[bytes, str]:
        return b"", "text/plain"


class NullNodeTracer:
    def span(self, name: str) -> AbstractContextManager[Any]:
        del name
        return nullcontext()
