"""One retry policy for every external call.

Exponential backoff with full jitter (so a fleet of workers does not retry in
lockstep), a bounded number of attempts, and retries only on errors that a
retry can fix: timeouts, connection errors, throttling, 5xx. A 400 is a bug or
bad input; retrying it only burns quota.
"""

import logging

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
