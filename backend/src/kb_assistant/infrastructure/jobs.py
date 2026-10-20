"""Job queue adapter (ADR-0009).

The API only *sends* tasks, by name: it never imports the worker's code, so
the two processes share a contract (task name + JSON arguments), not modules.
"""

import asyncio

from celery import Celery

from kb_assistant.domain.ids import TenantId, VersionId

INGEST_TASK = "kb.ingest_version"


def celery_producer(broker_url: str) -> Celery:
    app = Celery("kb_assistant_producer", broker=broker_url)
    app.conf.update(task_serializer="json", accept_content=["json"], task_ignore_result=True)
    return app


class CeleryJobQueue:
    def __init__(self, app: Celery) -> None:
        self._app = app

    async def enqueue_ingestion(self, tenant_id: TenantId, version_id: VersionId) -> None:
        # send_task is blocking network I/O to the broker.
        await asyncio.to_thread(
            self._app.send_task,
            INGEST_TASK,
            kwargs={"tenant_id": str(tenant_id), "version_id": str(version_id)},
        )
