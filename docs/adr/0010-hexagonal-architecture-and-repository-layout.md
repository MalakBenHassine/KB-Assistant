# 0010. Hexagonal architecture and monorepo layout

- **Status:** Proposed
- **Date:** 2026-10-20

## Context

The system mixes stable business rules (who may read what, when to abstain)
with volatile integrations (vendors, frameworks). The rules must be testable in
milliseconds without a network, and the integrations replaceable
([ADR-0006](0006-provider-abstraction.md)). The backend, the worker, the web
app, the evaluation harness and the deployment manifests change together and
should be reviewed together.

## Options considered

### A. Layered by framework (routers / services / models)
Familiar; business logic drifts into routers and ORM models, and services import
SDKs directly.

### B. Hexagonal (ports and adapters) with enforced dependency rules
`domain` ← `application` ← (`api`, `infrastructure`). More files; dependencies
are explicit and testable.

### C. Polyrepo (backend, frontend, infra in separate repositories)
Independent release cycles we do not need; cross-cutting changes become several
coordinated pull requests.

## Decision

Option B in a monorepo:

```
kb-assistant/
├── backend/                      # one Python package, two entry points (api, worker)
│   ├── src/kb_assistant/
│   │   ├── domain/               # entities, value objects, ports (Protocols), domain errors — no I/O
│   │   ├── application/          # use cases, authorization policies, graph/ (LangGraph)
│   │   ├── infrastructure/       # adapters: db/, vector/, llm/, jobs/, blob/, fakes/, observability/
│   │   ├── api/                  # FastAPI routers, schemas, error mapping, dependencies
│   │   ├── worker/               # Celery app and task shells
│   │   └── bootstrap.py          # composition root: settings → adapters → use cases
│   ├── migrations/               # Alembic
│   └── tests/{unit,integration,contract,e2e,security}/
├── web/                          # Next.js App Router, TypeScript, Tailwind
├── eval/                         # dataset.jsonl, runners, reports
├── deploy/{compose,helm,grafana}/
├── docs/{adr,ARCHITECTURE.md,SECURITY.md,EVALUATION.md}
└── .github/workflows/
```

Dependency rules, enforced in CI by `import-linter`:

- `domain` imports nothing from the project's other layers and no third-party
  I/O library.
- `application` imports `domain` only (plus `langgraph` inside `application.graph`).
- `api` and `worker` import `application` and `bootstrap`, never `infrastructure`
  directly.
- Only `infrastructure` imports vendor SDKs, SQLAlchemy, Redis, Celery.

Use cases are classes with explicit constructor dependencies; FastAPI's
dependency injection only resolves the composition root and the principal, so
routers stay a few lines: parse → call use case → map result.

## Consequences

- Domain and application tests need no containers; coverage target ≥ 80 % there
  is realistic.
- More indirection for simple CRUD; accepted, and kept proportionate (a
  repository method is fine, no "service" wrappers that add nothing).
- Domain errors map to RFC 7807 responses in one place (`api/errors.py`).

## Revisit when

The project splits into independently deployed services with separate owners.
