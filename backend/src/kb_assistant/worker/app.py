"""Celery application.

Phase 1 ships the process, its configuration and its health check; the
ingestion tasks arrive in Phase 2. The settings that matter for correctness
(at-least-once delivery, JSON only, bounded run time) are set here and tested.

Run with: `celery -A kb_assistant.worker.app worker`.
"""

from typing import Any

from celery import Celery
from celery.signals import setup_logging

from kb_assistant.bootstrap import configure_runtime
from kb_assistant.settings import WorkerSettings

TASK_SOFT_TIME_LIMIT_S = 300
TASK_TIME_LIMIT_S = 360


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

    return app


def _load_settings() -> WorkerSettings:
    return WorkerSettings()


@setup_logging.connect  # type: ignore[untyped-decorator]
def _configure_logging(**_: Any) -> None:
    configure_runtime(_load_settings())


# `celery -A` needs a module-level application; this module is the worker's entry point.
app = create_celery(_load_settings())
