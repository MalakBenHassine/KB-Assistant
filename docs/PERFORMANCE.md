# Performance

How the answering path behaves under concurrent use, how it is measured, and
the bug the first measurement found.

- Script: [`loadtest/chat.js`](../loadtest/chat.js) (k6 2.3.0)
- Run: `make up && make demo && make loadtest`

## What is measured

Four demo users, each asking questions their own departments answer (and a
few they do not: an unanswerable question and a greeting), through the API
as a client would: JSON answers, real PostgreSQL with row-level security,
pgvector, the LangGraph workflow and the persistence of every answer and its
audit entry. Virtual users ramp from 1 to 25 over 20 s, stay at 25 for 60 s,
then ramp down. The rate limits are raised for the run (`make loadtest` does
it and restores them): they are proven by tests, and here they would only
measure themselves.

The models are the offline ones (hashing embedder, extractive answers), so
these numbers are the **platform's** overhead. A hosted model adds its own
latency, typically seconds, on top; it is bounded by `KB_LLM__TIMEOUT_S` and
the circuit breaker.

Thresholds that fail the run: server errors under 1 %, failed requests under
1 %, 95th-percentile answer time under 1.5 s.

## Results

Docker Compose on one laptop (WSL 2, 8 cores), one API process, 2026-10-21.

| | First run | After the fix |
|---|---|---|
| Questions answered | 2,201 in 100 s (21.8/s) | 3,976 in 100 s (**39.5/s**) |
| Server errors (5xx) | 2.8 % | **0 %** |
| Answer time p50 / p95 / p99 | 383 ms / 1.06 s / **10.2 s** | 331 ms / 669 ms / **756 ms** |
| Slowest answer | 16 s | 946 ms |
| Thresholds | failed | **passed** |

The 14 % of questions without an answer are the ones designed for it (the
parking policy, a greeting): the abstention behaviour is the evaluation's
business ([EVALUATION.md](EVALUATION.md)), not this test's.

## What the first run found

At 25 concurrent users, 2.8 % of requests failed with
`QueuePool limit of size 10 overflow 10 reached` and the p99 reached 10 s,
the pool's 5-second wait plus the work.

Cause: the request-scoped database session that authenticates the caller
(`current_principal`) kept its transaction, and so its connection, open until
the response ended. Every question therefore held **two** connections, one
of them idle for the whole answer. With a hosted model, that idle connection
would be held for the seconds the model takes to write: the pool would run
out at a handful of concurrent users, whatever its size.

Fix: authentication is read-only, so the dependency ends its transaction as
soon as the principal is known; the connection returns to the pool, and the
row-level-security context stays bound for any later query of the same
request. A regression test
(`tests/e2e/test_connection_use.py`) checks that no connection is checked out
after authentication; it fails without the fix.

Throughput nearly doubled and the tail went from 10 s to under 1 s, without
touching the pool size: raising the pool would only have moved the failure to
a larger number of users, and multiplied the connections PostgreSQL must hold.

## Capacity planning notes

- One API process sustained 40 questions/s offline at 25 concurrent users with
  a 20-connection pool; each request now holds at most one connection at a
  time, and only while it queries.
- With a hosted model, concurrency is bounded by the model's latency and the
  provider's rate limits long before the database: the API's own work is
  under 1 s at p99.
- The Helm chart scales the API horizontally on CPU (2 to 6 replicas by
  default). PostgreSQL connections grow with replicas × (pool size + overflow):
  size `max_connections`, or put PgBouncer in transaction mode in front, before
  raising `maxReplicas`.
