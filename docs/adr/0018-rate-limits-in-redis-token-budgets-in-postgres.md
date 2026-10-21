# 0018. Rate limits in Redis, token budgets in Postgres

- **Status:** Proposed
- **Date:** 2026-10-21

## Context

Every question costs model tokens. A script, a stuck client or a curious
employee can burn a tenant's monthly spend in minutes and degrade the service
for every other tenant (SECURITY.md T8, OWASP LLM10). Two different questions
need answers: *how fast* may someone ask, and *how much* may a tenant spend.

## Options considered

### A. A limit in the API process (in memory)
Simple, but every replica counts separately: N replicas allow N times the limit.

### B. A fixed-window counter in Redis
One `INCR` per request; allows twice the limit around a window boundary.

### C. A token bucket in Redis, one Lua script
Atomic read-refill-spend on the server, using Redis's clock: replicas share
one limit and never disagree on time; bursts are allowed up to a size, then
the rate is smooth.

For spend: a counter in Redis (fast, but lost on eviction and outside the
tenant's backups), or a row per tenant and month in Postgres, incremented in
the same transaction that stores the answer.

## Decision

- **Rate:** C, per user and per tenant, on the question endpoint. Defaults: a
  burst of 5 and 10 per minute per user; 50 and 300 per minute per tenant
  (`KB_LIMITS__*`). A refusal is `429` with `Retry-After`.
- **Spend:** `tenant_usage(tenant, month)` in Postgres, an atomic upsert per
  answer; `tenant_settings.monthly_token_budget` (null: none; no row: the
  configured default). The check runs before the question is stored, so a
  blocked request costs nothing. A refusal is `429 budget_exhausted` with
  `Retry-After` until the 1st of next month (UTC).
- **Redis down:** the rate limit fails *open* and logs a warning. Spend is
  still bounded by the budget, which lives in Postgres; refusing all questions
  because a cache is unavailable would turn a Redis incident into an outage.

## Consequences

- A tenant admin sees the month's usage and sets the budget
  (`GET`/`PUT /v1/admin/settings`); each change is audited.
- The budget is checked before a question, not during it: one question can
  overshoot by its own size. Bounded by `max_llm_calls` and
  `answer_max_tokens`, and accepted.
- Offline models report zero tokens, so the budget only bites with a hosted
  model; the tests use a budget of 0 to exercise the path.

## Revisit when

Several tenants need different rates (move the limits into
`tenant_settings`), or Redis availability makes fail-open unacceptable for a
regulated tenant.
