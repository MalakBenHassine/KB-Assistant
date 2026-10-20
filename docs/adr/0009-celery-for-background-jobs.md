# 0009. Celery for background jobs (deviation: not arq)

- **Status:** Proposed
- **Date:** 2026-10-20

## Context

Ingestion (parse, chunk, embed, upsert) takes seconds to minutes and must not run
in the request path. The specification allows "Celery or arq" with Redis.
Requirements: at-least-once delivery, retries with backoff, per-task time limits,
visibility into failures, and active maintenance for a system meant to run for
years.

Facts checked on 2026-10-20: the arq project states on PyPI that it is in
**maintenance-only mode** ([pypi.org/project/arq](https://pypi.org/project/arq)).

## Options considered

### A. arq
Async-native and light, which suits an async codebase. But maintenance-only
status means no new features and slower fixes; choosing it for a new system is
a known risk with no offsetting benefit we cannot get elsewhere.

### B. Celery (Redis broker)
The most widely deployed Python task queue; mature retries, time limits, rate
limits, routing, monitoring (Flower, Prometheus exporters). Its task functions
are synchronous, so async use cases need an event-loop bridge.

### C. Taskiq or SAQ
Async-native and actively developed, but much smaller ecosystems; fewer
engineers and operators know them.

## Decision

Option B, Celery with Redis as broker, behind a `JobQueue` port.

- The API depends on `domain.ports.JobQueue.enqueue_ingestion(version_id)`; only
  `infrastructure/jobs/celery_queue.py` imports Celery.
- Tasks are thin: they call the async `IngestDocumentVersion` use case through
  one event loop per worker process, created in `worker_process_init`, so
  connection pools are reused across tasks instead of rebuilt by `asyncio.run`.
- `acks_late=True` and `task_reject_on_worker_lost=True` for at-least-once
  delivery; the use case is idempotent (deterministic vector IDs, unique chunk
  keys, status transitions guarded by the current status), so redelivery is safe.
- Soft and hard time limits per task; `autoretry_for` only on transient errors,
  with exponential backoff and jitter; the final failure writes `failed` with an
  error code and `retryable` flag.
- The task payload is a version ID only. File bytes are read from object
  storage, never passed through the broker.
- Worker concurrency is bounded per tenant with a Redis semaphore so one tenant's
  bulk upload cannot starve the others (SECURITY.md T8).

## Consequences

- A deviation from the specification's "async end to end" in the worker's
  outermost layer: the task shell is synchronous, everything it calls is async.
- Celery's configuration surface is large; we keep it in one module with tests
  for the settings that matter (acks, time limits, serializer = JSON only).

## Revisit when

Celery's sync model becomes a bottleneck (many concurrent I/O-bound tasks per
process), or an async-native queue reaches comparable maturity.
