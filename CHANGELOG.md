# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-10-22

### Added
- A free public demo: read-only demo mode for the API (`KB_DEMO__READ_ONLY`, an
  allowlist of the writes a visitor needs), demo accounts on the sign-in page,
  a nightly reset that also removes emptied conversations, and Oracle Cloud
  values with cert-manager and Let's Encrypt (`deploy/oracle`); a single-node
  k3s test environment (`deploy/vm`).
- Operations: Prometheus metrics behind a Telemetry port (RED per route
  template, answers, abstentions, tokens, rate-limit refusals, ingestion),
  OpenTelemetry tracing with a span per graph node and trace ids in logs, a
  hardened Helm chart (restricted pods, default-deny NetworkPolicies, HPAs,
  PDBs, migration hook, retention and audit-head CronJobs, values schema),
  a Grafana dashboard, alerts with a runbook, a k6 load test, and CI
  validation of the chart (ADR-0021).
- Web app: sign-in, chat with answers streamed sentence by sentence and
  numbered sources (document, headings, pages), department filter, feedback,
  conversation list and organization switcher; document upload with indexing
  status and quarantine warnings; administration with analytics, a daily
  chart, knowledge gaps, the monthly budget and the audit log (verify, CSV).
  Next.js BFF with path-scoped `HttpOnly` cookies, single-flight refresh,
  origin checks and a per-response nonce CSP (ADR-0020); `make demo` loads the
  evaluation corpus into the demo tenants; CI drives the BFF end to end.
- Governance: feedback on answers (one rating per person, reasons, comment);
  rate limits per user and per tenant (Redis token bucket, 429 with
  Retry-After); monthly token budgets and usage per tenant; analytics with
  answer rate, abstentions by reason, satisfaction, latency, tokens, daily
  series and knowledge gaps, scoped for department admins; a hash-chained
  audit log with paging, verification and formula-safe CSV export;
  conversation memory and automatic titles (migration 0005, ADR-0018, ADR-0019).
- Evaluation: a 30-document corpus (2 tenants, 3 departments, conflicting values
  across tenants), 86 questions with their askers (answerable, restricted,
  unanswerable, out of scope, chitchat), hit@k, MRR, answer accuracy, citation
  precision, lexical faithfulness, abstention accuracy and leak counts, threshold
  calibration, Markdown and JSON reports, and a CI quality gate (`make eval`).
- Prompt-injection defence at ingestion: paragraphs that address the assistant
  are quarantined from the index and counted on the version
  (`quarantined_passages`, migration 0004, ADR-0017); the evaluation set plants
  an injection and gates on its claims never reaching an answer.

- Question answering: a LangGraph workflow (classify, rewrite, retrieve within
  the caller's scope, grade, bounded retry, rerank, generate, validate citations,
  clarify or abstain), sentence-level citation checking, nonce-delimited sources,
  SSE streaming, conversations and messages that record retrieved and cited
  chunks, scores, model, prompt version, thresholds and latency; audit entry per
  question. OpenAI Responses API adapter with a circuit breaker, offline
  extractive model, Pinecone reranker.
- Document ingestion: upload with content-based type detection and size
  limits, PDF/DOCX/Markdown/HTML parsing, text cleaning (invisible characters,
  prompt-delimiter neutralisation, running headers and footers), structure-aware
  token-bounded chunking with overlap, batched embeddings with retries, pgvector
  and Pinecone adapters behind one contract, idempotent and versioned indexing,
  retry endpoint. Celery worker runs the pipeline; SeaweedFS stores originals.
- Backend (FastAPI, hexagonal layout enforced by import contracts):
  login, rotating refresh tokens with reuse detection, logout, invitations and
  registration, tenant switching, departments, memberships, profile.
- RS256 access tokens with a JWKS endpoint, so an OIDC provider can replace the
  issuer later; Argon2id password hashing off the event loop.
- PostgreSQL schema with row-level security on every tenant-scoped table, a
  non-owner application role, and an append-only audit log.
- RFC 7807 errors, request ids, security headers, JSON logs with redaction,
  liveness and readiness probes.
- Celery worker process with at-least-once settings (tasks arrive in Phase 2).
- Next.js 16 web app skeleton with baseline security headers.
- Container images (non-root; no package manager in the web runtime) and a
  compose stack started by `make up`, with demo data from `make seed`.
- CI (lint, `mypy --strict`, tests against real Postgres and Redis, coverage
  gate, image scans, compose smoke test), security workflow (gitleaks, Trivy,
  dependency review, CodeQL) and CD (signed multi-arch images, Vercel deploy).
- Architecture document, threat model, deployment guide, ADRs 0001–0014.

### Fixed
- Authentication no longer holds a database connection until the response
  ends. Under 25 concurrent users this exhausted the pool (2.8 % errors, p99
  10 s); after the fix: no errors, p99 0.76 s, throughput doubled.

### Changed
- `retrieval.min_score` defaults to the calibrated 0.07 instead of the 0.2
  placeholder, which refused 9 % of answerable questions on the evaluation set.

[Unreleased]: https://github.com/MalakBenHassine/KB-Assistant/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/MalakBenHassine/KB-Assistant/releases/tag/v1.0.0
