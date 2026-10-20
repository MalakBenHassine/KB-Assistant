"""Celery application (ADR-0009).

Celery tasks are synchronous; the ingestion use case is async. Each worker
process gets one event loop and one container, created when the process
starts (`worker_process_init`), so connection pools are reused across tasks
instead of being rebuilt by `asyncio.run` for every job.

Run with: `celery -A kb_assistant.worker.app worker`.
"""

import asyncio
import logging
from typing import Any
from uuid import UUID

from celery import Celery, Task
from celery.signals import setup_logging, worker_process_init, worker_process_shutdown

from kb_assistant.bootstrap import WorkerContainer, build_worker_container, configure_runtime
from kb_assistant.domain.ids import TenantId, VersionId
from kb_assistant.settings import WorkerSettings

INGEST_TASK = "kb.ingest_version"  # the contract with the API (infrastructure/jobs.py)
TASK_SOFT_TIME_LIMIT_S = 300
TASK_TIME_LIMIT_S = 360
RETRY_BACKOFF_S = (30, 120, 600)

log = logging.getLogger(__name__)


class _Runtime:
    """Per-process state: the composition root of a worker process."""

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.container: WorkerContainer | None = None

    def start(self, settings: WorkerSettings) -> None:
        self.loop = asyncio.new_event_loop()
        self.container = build_worker_container(settings)

    def stop(self) -> None:
        if self.loop is not None and self.container is not None:
            self.loop.run_until_complete(self.container.aclose())
            self.loop.close()
        self.loop, self.container = None, None

    def ensure_started(self) -> tuple[asyncio.AbstractEventLoop, WorkerContainer]:
        if self.loop is None or self.container is None:
            # Solo pool or eager mode: no process-init signal was sent.
            self.start(_load_settings())
        if self.loop is None or self.container is None:
            raise RuntimeError("worker runtime failed to start")
        return self.loop, self.container


runtime = _Runtime()


def _load_settings() -> WorkerSettings:
    return WorkerSettings()


def create_celery(settings: WorkerSettings) -> Celery:
    broker = settings.redis.url.get_secret_value()
    app = Celery("kb_assistant", broker=broker)
    app.conf.update(
        # JSON only: pickle would let anyone who can write to Redis run code.
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        task_ignore_result=True,
        # At-least-once: a task is acknowledged after it finishes, and requeued
        # if the worker dies mid-way. Tasks must therefore be idempotent.
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_soft_time_limit=TASK_SOFT_TIME_LIMIT_S,
        task_time_limit=TASK_TIME_LIMIT_S,
        broker_connection_retry_on_startup=True,
        worker_hijack_root_logger=False,
        worker_send_task_events=True,
    )

    @app.task(name="kb.ping")  # type: ignore[untyped-decorator]  # Celery ships no type hints
    def ping() -> str:
        return "pong"

    @app.task(name=INGEST_TASK, bind=True, max_retries=len(RETRY_BACKOFF_S))  # type: ignore[untyped-decorator]
    def ingest_version(self: Task, tenant_id: str, version_id: str) -> str:
        loop, container = runtime.ensure_started()
        outcome = loop.run_until_complete(
            container.ingest().execute(TenantId(UUID(tenant_id)), VersionId(UUID(version_id)))
        )
        if outcome.retry:
            attempt = min(self.request.retries, len(RETRY_BACKOFF_S) - 1)
            raise self.retry(countdown=RETRY_BACKOFF_S[attempt])
        return outcome.status.value if outcome.status else "skipped"

    return app


@setup_logging.connect  # type: ignore[untyped-decorator]
def _configure_logging(**_: Any) -> None:
    configure_runtime(_load_settings())


@worker_process_init.connect  # type: ignore[untyped-decorator]
def _start_process(**_: Any) -> None:
    runtime.start(_load_settings())


@worker_process_shutdown.connect  # type: ignore[untyped-decorator]
def _stop_process(**_: Any) -> None:
    runtime.stop()


# `celery -A` needs a module-level application; this module is the worker's entry point.
app = create_celery(_load_settings())
