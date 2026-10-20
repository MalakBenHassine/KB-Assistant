"""One retry policy for every external call.

Exponential backoff with full jitter (so a fleet of workers does not retry in
lockstep), a bounded number of attempts, and retries only on errors that a
retry can fix: timeouts, connection errors, throttling, 5xx. A 400 is a bug or
bad input; retrying it only burns quota.
"""

import logging
from collections.abc import Callable

from tenacity import (
    AsyncRetrying,
    before_sleep_log,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

log = logging.getLogger(__name__)


def retrying(
    *,
    attempts: int,
    retry_on: tuple[type[BaseException], ...],
    max_wait_s: float = 20.0,
) -> AsyncRetrying:
    return AsyncRetrying(
        stop=stop_after_attempt(attempts),
        wait=wait_random_exponential(multiplier=0.5, max=max_wait_s),
        retry=retry_if_exception_type(retry_on),
        reraise=True,
        before_sleep=before_sleep_log(log, logging.WARNING),
    )


class CircuitOpenError(Exception):
    """Raised immediately while the breaker is open, without calling the provider."""


class CircuitBreaker:
    """Stop calling a provider that keeps failing, then probe it again after a pause.

    closed → (N consecutive failures) → open → (reset_after elapses) → half-open:
    the next call is a probe; success closes the breaker, failure re-opens it.
    During an outage this turns a 60-second timeout per request into an
    immediate, clearly labelled "temporarily unavailable" answer.
    """

    def __init__(self, *, failure_threshold: int, reset_after_s: float, clock: "Monotonic") -> None:
        self._threshold = failure_threshold
        self._reset_after = reset_after_s
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        if self._clock() - self._opened_at >= self._reset_after:
            return "half_open"
        return "open"

    def before_call(self) -> None:
        if self.state == "open":
            raise CircuitOpenError

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self.state == "half_open" or self._failures >= self._threshold:
            self._opened_at = self._clock()


Monotonic = Callable[[], float]
