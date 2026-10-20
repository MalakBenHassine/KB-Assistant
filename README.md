# KB Assistant

> A multi-tenant enterprise knowledge-base assistant. Employees ask questions in
> natural language and get grounded answers with citations, drawn **only** from
> the documents they are authorized to read.

[![CI](https://github.com/MalakBenHassine/KB-Assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/MalakBenHassine/KB-Assistant/actions/workflows/ci.yml)
[![Security](https://github.com/MalakBenHassine/KB-Assistant/actions/workflows/security.yml/badge.svg)](https://github.com/MalakBenHassine/KB-Assistant/actions/workflows/security.yml)
[![CD](https://github.com/MalakBenHassine/KB-Assistant/actions/workflows/cd.yml/badge.svg)](https://github.com/MalakBenHassine/KB-Assistant/actions/workflows/cd.yml)

**Status:** Phase 3 of 7 done. Employees can ask questions and get answers
streamed sentence by sentence, each sentence cited, drawn only from what they
may read. Evaluation and threshold calibration are next.

## Why this is not another "chat with your PDF"

| A demo RAG app | KB Assistant |
|----------------|--------------|
| Retrieves from everything, filters (maybe) afterwards | Authorization is enforced **before** retrieval, then checked twice more; an unauthorized chunk never reaches the prompt |
| Always answers | Grades its evidence, retries a bounded number of times, then asks a clarifying question or says "I don't know" |
| "Sources" are decoration | Every sentence must cite a retrieved chunk; uncited sentences are dropped |
| Thresholds copied from a blog | Thresholds calibrated on an evaluation set, with a CI gate on regressions |
| One user | Tenants, departments, roles, audit log, rate limits, per-tenant token budgets |

## What is already proven

- **Tenant isolation in the database itself.** Every tenant-scoped table has a
  PostgreSQL row-level-security policy. The API connects as a role that does
  not own the tables and cannot bypass RLS, so a forgotten `WHERE tenant_id`
  returns nothing instead of another company's rows. Tested as that role.
- **Identity separate from membership** ([ADR-0012](docs/adr/0012-identity-separate-from-tenant-membership.md)).
  A consultant can belong to two companies; a token is valid for exactly one,
  and being an admin in one grants nothing in the other.
- **Authorization data is never trusted from the token.** Role and departments
  are read from the membership on every request: a demotion or a removal takes
  effect on the next call, not when the token expires.
- **Refresh tokens rotate, and reuse is detected.** Replaying an old refresh
  token revokes the whole session family.
- **No endpoint is public by omission.** A test reads the OpenAPI schema and
  fails if any operation is unauthenticated without being on a reviewed list.
- **A hexagonal codebase, enforced.** Import contracts in CI keep frameworks and
  vendor SDKs out of the business rules.
- **A delivery pipeline you could audit.** Actions pinned by SHA, multi-arch
  images with SBOM and provenance, signed with cosign keyless, deployed only
  after CI is green.

- **Ingestion that is safe to retry.** Uploads are typed by their bytes, not
  their name; the worker parses PDF, DOCX, Markdown and HTML into sections,
  chunks them by tokens with overlap, embeds in batches and indexes. Every step
  is idempotent, a new version archives the old one without deleting it, and
  Postgres stays the source of truth over the vector index
  ([ADR-0015](docs/adr/0015-ingestion-consistency-and-object-storage.md)).
- **Isolation proven at the retrieval layer.** The isolation tests run the real
  workflow against a vector store that *leaks on purpose* (it ignores the scope
  and returns every tenant's vectors) and assert on the prompt sent to the
  model: Postgres re-checks tenant, department and current version before any
  chunk is used, for every combination of departments.
- **An explicit, bounded workflow.** A LangGraph state machine classifies the
  question, rewrites it, retrieves within the caller's scope, grades the
  evidence, retries once, then answers with citations, asks for clarification
  or says it doesn't know. Every sentence must cite a retrieved source or it is
  dropped before the user sees it.
- **Runs without paid keys.** pgvector and a deterministic offline embedder
  stand in for Pinecone and OpenAI, behind the same ports and contract tests.

## Run it

Requirements: Docker with Compose, [uv](https://docs.astral.sh/uv/), `make`, `openssl`.

```bash
make up      # generates .env and a signing key on first run, builds, starts, waits until healthy
make seed    # two companies (Acme, Globex), three departments each, seven users
```

Then open the API documentation at http://127.0.0.1:8000/docs, sign in, and
upload a document with `POST /v1/documents`; its status moves from `pending`
to `indexed` within seconds. Then create a conversation and ask:

```bash
curl -N http://127.0.0.1:8000/v1/conversations/$CONVERSATION/messages \
  -H "Authorization: Bearer $TOKEN" -H 'Accept: text/event-stream' \
  -H 'content-type: application/json' -d '{"question": "How many days of leave do I get?"}'
``` Every demo user
has the password stored as `KB_SEED_PASSWORD` in your generated `.env`.

| User | Company | Role | Departments |
|------|---------|------|-------------|
| alice@acme.example | Acme | tenant_admin | all |
| bob@acme.example | Acme | dept_admin | engineering |
| carol@acme.example | Acme | employee | hr |
| dave@acme.example | Acme | employee | engineering, product |
| erin@globex.example | Globex | tenant_admin | all |
| frank@globex.example | Globex | employee | engineering |
| grace@consulting.example | Acme **and** Globex | employee / dept_admin | engineering / product |

`make help` lists the other targets (`down`, `clean`, `logs`, `lint`, `test`, `check`).

## Develop

```bash
make install   # backend and web dependencies, git hooks
make check     # everything CI runs: format, lint, mypy --strict, import contracts, tests, coverage gate
```

The backend tests start real PostgreSQL and Redis containers (testcontainers)
and run the real Alembic migrations, so row-level security is exercised exactly
as in production.

## Design documents

- [Architecture](docs/ARCHITECTURE.md): C4 views, the LangGraph workflow, the
  request sequence, the three isolation checks, ingestion, data model.
- [Security and threat model](docs/SECURITY.md): twelve threats, their
  mitigations, and the test that proves each one.
- [Deployment](docs/DEPLOYMENT.md): the pipeline, one-time setup, verifying an image signature.
- [Architecture Decision Records](docs/adr/README.md): fourteen decisions, each
  with the options weighed and when to revisit it.

## Stack

Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2 (async) · Alembic · PostgreSQL
with row-level security · Redis · Celery · Next.js 16 · TypeScript · Tailwind ·
Docker · GitHub Actions · cosign · Trivy · CodeQL · LangGraph · Pinecone (pgvector
locally) · OpenAI · SeaweedFS. Coming next: OpenTelemetry, Prometheus, Grafana, Helm.

## Roadmap

| Phase | Scope | Status |
|-------|-------|--------|
| 0 | Design: architecture, ADRs, threat model | Done |
| 1 | Foundations: auth, tenancy, RLS, compose stack, CI/CD | Done |
| 2 | Ingestion pipeline: upload, parse, chunk, embed, versioning | Done |
| 3 | Retrieval and the LangGraph workflow, SSE streaming, isolation tests | Done |
| 4 | Evaluation: dataset, metrics, threshold calibration, CI gate | Next |
| 5 | Enterprise features: feedback, analytics, audit log viewer, memory, limits | — |
| 6 | Frontend: chat with citations, document management, dashboard | — |
| 7 | Production readiness: Helm, tracing, dashboards, load test | — |

## License

[MIT](LICENSE)
