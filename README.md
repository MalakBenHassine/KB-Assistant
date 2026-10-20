# KB Assistant

> A multi-tenant enterprise knowledge-base assistant. Employees ask questions in
> natural language and get grounded answers with citations, drawn **only** from
> the documents they are authorized to read.

**Status:** Phase 0 — design under review. No application code yet, on purpose:
the decisions that are expensive to reverse are written down first.

## Why this is not another "chat with your PDF"

| A demo RAG app | KB Assistant |
|----------------|--------------|
| Retrieves from everything, filters (maybe) afterwards | Authorization is enforced **before** retrieval, then checked twice more; an unauthorized chunk never reaches the prompt |
| Always answers | Grades its evidence, retries a bounded number of times, then asks a clarifying question or says "I don't know" |
| "Sources" are decoration | Every sentence must cite a retrieved chunk; uncited sentences are dropped |
| Thresholds copied from a blog | Thresholds calibrated on an evaluation set, with a CI gate on regressions |
| One user | Tenants, departments, roles, audit log, rate limits, per-tenant token budgets |

## Design documents

- [Architecture](docs/ARCHITECTURE.md): C4 context and containers, the LangGraph
  workflow, the request sequence, the isolation model, ingestion, data model.
- [Security and threat model](docs/SECURITY.md): twelve threats, their
  mitigations, and the test that proves each mitigation.
- [Architecture Decision Records](docs/adr/README.md): vector store, tenant
  isolation, chunking, authentication, provider abstraction, orchestration,
  token handling, background jobs, code layout, reranking, identity and
  membership, document sources.

## Planned stack

Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2 (async) · Alembic · LangGraph ·
Pinecone (pgvector locally) · OpenAI · PostgreSQL with row-level security · Redis ·
Celery · Next.js (App Router) · TypeScript · Tailwind · OpenTelemetry · Prometheus ·
Grafana · Langfuse · Docker · Kubernetes (Helm) · GitHub Actions

## Roadmap

| Phase | Scope | Status |
|-------|-------|--------|
| 0 | Design: architecture, ADRs, threat model | In review |
| 1 | Foundations: monorepo, compose, config, migrations, auth + RBAC, CI | — |
| 2 | Ingestion pipeline: upload, parse, chunk, embed, versioning | — |
| 3 | Retrieval and the LangGraph workflow, SSE streaming, isolation tests | — |
| 4 | Evaluation: dataset, metrics, threshold calibration, CI gate | — |
| 5 | Enterprise features: feedback, analytics, audit log, memory, limits | — |
| 6 | Frontend: chat with citations, document management, dashboard | — |
| 7 | Production readiness: Helm, tracing, dashboards, load test | — |

## License

[MIT](LICENSE)
