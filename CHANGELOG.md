# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Evaluation: a 30-document corpus (2 tenants, 3 departments, conflicting values
  across tenants), 86 questions with their askers (answerable, restricted,
  unanswerable, out of scope, chitchat), hit@k, MRR, answer accuracy, citation
  precision, lexical faithfulness, abstention accuracy and leak counts, threshold
  calibration, Markdown and JSON reports, and a CI quality gate (`make eval`).

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

### Changed
- `retrieval.min_score` defaults to the calibrated 0.07 instead of the 0.2
  placeholder, which refused 9 % of answerable questions on the evaluation set.
