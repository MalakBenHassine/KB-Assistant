# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
