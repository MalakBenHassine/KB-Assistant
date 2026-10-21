# Runbook

What to do when an alert fires. Each alert in the Helm chart
(`deploy/helm/kb-assistant/templates/monitoring.yaml`) links to its section.
Dashboards: the "KB Assistant" Grafana dashboard. Every log line carries
`request_id`, and `trace_id` when tracing is on; error responses return the
`request_id` to the user, so a support ticket leads straight to the logs.

Logs never contain question, answer or document text (SECURITY.md T5). To
see what a user asked, a tenant admin reads the audit log.

## API errors

`KBApiErrorRate`: more than 5 % of API requests fail for 10 minutes.

1. Dashboard, "API requests by route": one route, or all of them?
2. All routes: check readiness (`kubectl get pods`, `/readyz` reports
   `database` and `redis` separately). A failing dependency is the usual cause.
3. One route: the logs for `http.unhandled_error` give the exception type and
   the `request_id`.
4. `sqlalchemy.exc.TimeoutError: QueuePool limit ... reached`: connections are
   exhausted. Check for a request that holds a session across slow work (the
   bug in [PERFORMANCE.md](PERFORMANCE.md)), then PostgreSQL
   `max_connections` against replicas × (pool size + overflow).

## Slow answers

`KBAnswerLatencyHigh`: the 95th-percentile answer takes more than 20 s.

1. With tracing on, open a slow trace: one span per graph node shows whether
   `retrieve`, `rerank` or `generate` is slow.
2. `generate` slow: the model provider. Check its status page; the circuit
   breaker opens after repeated failures and answers "temporarily unavailable"
   quickly instead of waiting.
3. `retrieve` slow: the vector store (Pinecone latency, or pgvector without its
   HNSW index after a restore: `\d vector_records` must list it).

## Abstention spike

`KBAbstentionSpike`: more than half of the questions get no answer for 30 min.

1. Dashboard, "Abstentions by reason".
2. `no_relevant_documents` or `weak_evidence` everywhere at once: retrieval is
   broken. Usual causes: the embedding model changed without re-indexing
   (vectors from two models are not comparable), the vector index is empty,
   or `KB_RETRIEVAL__MIN_SCORE` was not recalibrated for the model
   ([EVALUATION.md](EVALUATION.md) §3).
3. For one tenant only: a knowledge gap. Administration → Knowledge gaps lists
   the unanswered questions; this is a content problem, not an incident.

## Model outage

`KBAssistantUnavailable`: users see "temporarily unavailable".

1. Provider status page. The logs give the provider's exception type, never
   the prompt.
2. Nothing to restart: the circuit breaker retries on its own after
   `KB_LLM__BREAKER_RESET_S`. Questions asked meanwhile were not charged to
   any budget beyond their own failed attempt.
3. Prolonged outage: switching `KB_LLM__PROVIDER` to another adapter is a
   configuration change and a rollout, not a code change (ADR-0006).

## Ingestion failures

`KBIngestionFailures`: more than three documents failed to index in 30 min.

1. Documents page (or `GET /v1/documents/{id}`): each failed version shows a
   code, a message and whether it is retryable.
2. `unreadable_document` or `empty_document` (a scan without text, for
   instance): the file itself; the uploader must fix it. Not an incident.
3. `<provider>_unavailable` (embeddings, vector store, object storage) or
   `internal_error`, retryable: a dependency. The worker retries with back-off (30 s, 2 min, 10 min); after
   that, an admin retries from the Documents page once the dependency is back.
4. Many at once: check the worker pods and their egress (NetworkPolicy allows
   ports 5432, 6379 and 443 only).

## Hydration rejections

`KBHydrationRejections`: more than 50 vector hits per hour refused by the
PostgreSQL re-check.

Each rejection is a chunk the vector store returned but PostgreSQL refused to
serve: its version is archived, or it belongs to another tenant or department.

1. Right after documents were re-uploaded: expected. Old versions are archived
   at once and their vectors deactivated shortly after (ADR-0015); the count
   falls back to zero.
2. Persistent: the vector index disagrees with PostgreSQL. That is the leak the
   re-check exists to stop (SECURITY.md T1): no foreign chunk reached a user,
   but find out why. Compare `vector_records.active` with the versions'
   status, and look for a deactivation that failed in the worker logs
   (`ingestion.deactivate_failed`).

## Audit chain broken

`GET /v1/admin/audit-log/verify` reports `valid: false`.

1. The response names the first broken sequence number and why: missing,
   modified, or a broken link.
2. The application role cannot change or delete audit rows; a broken chain
   means someone with the owner role, or a restore from an older backup, did.
   Treat it as a security incident.
3. Compare the head hash with the last `audit.head` line kept by the hourly
   job: if the table's chain ends earlier than the kept head, entries were
   removed from the end.
