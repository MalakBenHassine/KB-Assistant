# KB Assistant

> A multi-tenant enterprise knowledge-base assistant. Employees ask questions in
> natural language and get grounded answers with citations, drawn **only** from
> the documents they are authorized to read.

[![CI](https://github.com/MalakBenHassine/KB-Assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/MalakBenHassine/KB-Assistant/actions/workflows/ci.yml)
[![Security](https://github.com/MalakBenHassine/KB-Assistant/actions/workflows/security.yml/badge.svg)](https://github.com/MalakBenHassine/KB-Assistant/actions/workflows/security.yml)
[![CD](https://github.com/MalakBenHassine/KB-Assistant/actions/workflows/cd.yml/badge.svg)](https://github.com/MalakBenHassine/KB-Assistant/actions/workflows/cd.yml)

**Status: v1.0.0, complete.** A web app where employees ask questions and get
cited answers streamed sentence by sentence, drawn only from what they may
read; admins manage documents and see analytics, knowledge gaps, budgets and a
tamper-evident audit log. Every pull request is measured on an 88-question
evaluation set; every merge publishes signed images; the Helm chart is verified
on a real k3s cluster. What is *not* proven is listed under
[Known limits](#known-limits).

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
  images with SBOM and provenance, signed with cosign keyless, published only
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
- **Quality measured, thresholds calibrated, regressions blocked.** 88 questions,
  each with its asker, over 31 documents from two tenants that disagree on
  purpose (25 vs 22 days of leave), one of them carrying a planted prompt
  injection that must never reach an answer. The gate checks hit@k, MRR, answer accuracy,
  citation precision, abstention and leaks on every pull request, and
  `min_score` is the measured middle of its best interval, not a guess
  ([EVALUATION.md](docs/EVALUATION.md)).

  | hit@5 | MRR | Answer accuracy | Abstention accuracy | Leaked chunks |
  |---|---|---|---|---|
  | 100 % | 0.991 | 94.6 % | 98.9 % | 0 |

  *Offline profile (lexical embedder, extractive model): a pipeline baseline.*
  With real local models (nomic-embed-text and qwen3:4b through Ollama, free),
  hit@1 is 100 %, citation precision 100 %, abstention recall 100 %, still 0
  leaks, and 55 of 56 answers are right when read by hand
  ([EVALUATION.md §5.1](docs/EVALUATION.md#51-local-models-the-ollama-profile)).
- **Run like a product, not a demo.** Rate limits per user and tenant (Redis
  token buckets, `429` + `Retry-After`), monthly token budgets per tenant,
  feedback on every answer, analytics with **knowledge gaps** (the questions
  your documents do not answer, most asked first), and an audit log chained by
  SHA-256 so an edited or deleted entry is located by `GET /v1/admin/audit-log/verify`
  ([ADR-0018](docs/adr/0018-rate-limits-in-redis-token-budgets-in-postgres.md),
  [ADR-0019](docs/adr/0019-a-per-tenant-hash-chain-makes-the-audit-log-tamper-evident.md)).
- **A web app that keeps tokens out of the browser.** Next.js as a
  backend-for-frontend: tokens live in `HttpOnly` cookies scoped by path, the
  refresh is single-flight (the API revokes a session on refresh-token reuse),
  every page carries a per-response CSP nonce, and answers are rendered as text
  so a document can never inject markup
  ([ADR-0020](docs/adr/0020-render-every-page-per-request-for-a-nonce-based-csp.md)).
- **Operable, and measured under load.** Prometheus metrics with bounded labels,
  OpenTelemetry traces with a span per graph node, a Grafana dashboard, alerts
  that each link to a [runbook](docs/RUNBOOK.md) section, and a Helm chart that is
  restricted by default (non-root, read-only, default-deny network) and validated
  in CI. The first k6 run found a database connection held for the whole of
  every request; the fix took 25 concurrent users from 2.8 % errors and a 10 s
  p99 to 0 errors and 0.76 s, at twice the throughput
  ([PERFORMANCE.md](docs/PERFORMANCE.md)).
- **Runs without paid keys.** pgvector and a deterministic offline embedder
  stand in for Pinecone and OpenAI, behind the same ports and contract tests;
  Ollama runs real models locally through the same adapters
  ([ADR-0022](docs/adr/0022-local-models-through-ollama-zero-padded-to-the-index-width.md)).

## Run it

Requirements: Docker with Compose, [uv](https://docs.astral.sh/uv/), `make`, `openssl`, `jq`.

```bash
make up      # generates .env and a signing key on first run, builds, starts, waits until healthy
make demo    # two companies, three departments each, seven users, and 31 documents indexed
```

Open http://localhost:3000 and sign in as one of the demo users below; the
password is `KB_SEED_PASSWORD` in your generated `.env`. Ask Carol's question,
"How many days of paid leave do I get?", then sign in as Alice to see the
analytics, the knowledge gaps and the audit log. The API documentation is at
http://127.0.0.1:8000/docs.

| User | Company | Role | Departments |
|------|---------|------|-------------|
| alice@acme.example | Acme | tenant_admin | all |
| bob@acme.example | Acme | dept_admin | engineering |
| carol@acme.example | Acme | employee | hr |
| dave@acme.example | Acme | employee | engineering, product |
| erin@globex.example | Globex | tenant_admin | all |
| frank@globex.example | Globex | employee | engineering |
| grace@consulting.example | Acme **and** Globex | employee / dept_admin | engineering / product |

Everything runs offline by default: a lexical embedder and an extractive model
that quotes sources. `KB_EMBEDDINGS_PROVIDER=openai`, `KB_LLM_PROVIDER=openai`
and `OPENAI_API_KEY` in `.env` switch to the hosted models. With
[Ollama](https://ollama.com) installed, real models run locally for free:

```bash
ollama pull nomic-embed-text && ollama pull qwen3:4b
make clean && make up-ollama && make demo
```

`make help` lists the other targets (`down`, `clean`, `logs`, `lint`, `test`, `eval`, `check`).

## Deploy

| Where | How | State |
|---|---|---|
| Laptop | Docker Compose (`make up && make demo`) | the development stack; CI starts it on every push and drives the web app end to end |
| Container registry | GitHub Actions after a green CI on `main` | multi-arch images (amd64, arm64) on GHCR, signed with cosign, with SBOM and provenance |
| Kubernetes | Helm chart (`deploy/helm/kb-assistant`), `helm upgrade --install` | **verified on k3s** (Kubernetes 1.36) on a single VM: restricted Pod Security, default-deny NetworkPolicies, migration hook, TLS ingress ([`deploy/vm`](deploy/vm/README.md)) |
| Public demo | Oracle Cloud Always Free, DuckDNS, Let's Encrypt, read-only demo mode | written and validated in CI ([`deploy/oracle`](deploy/oracle/README.md)), not running |

Deployment to a cluster is a manual `helm upgrade`; a GitOps controller that
verifies image signatures before pulling (Flux) is the next step
([DEPLOYMENT.md](docs/DEPLOYMENT.md)).

## Known limits

- **Pinecone is verified by its contract, not in production.** The six
  vector-store contract tests (ranking, department scope, tenant isolation,
  deactivated versions, idempotent upsert, empty scope) pass against a real
  Pinecone serverless index on the free tier, as they do against pgvector. CI
  has no key, so they run there on pgvector only.
- **OpenAI is implemented, not exercised.** No key was used. Its adapters
  are unit-tested against fakes, and they run for real against local models
  through Ollama's compatible API; CI and k3s use the offline models.
- **The CI gate measures the pipeline, not a language model.** It runs the
  offline profile, which matches words and quotes sentences. Local models were
  measured once on a laptop GPU (§5.1 of [EVALUATION.md](docs/EVALUATION.md));
  their answers take about 30 s, and the lexical fact matching undercounts
  reworded answers.
- **No OCR**: a scanned PDF has no text to index and fails with a clear error.
- **Upload only**: connectors to SharePoint or Drive sit behind a port and are
  not written ([ADR-0013](docs/adr/0013-upload-first-connectors-behind-a-source-port.md)).
- **English evaluation set**: the pipeline handles other languages; the
  evaluation set does not test them.

## Develop

```bash
make install   # backend and web dependencies, git hooks
make check     # format, lint, mypy --strict, import contracts, tests, coverage gate, evaluation
make chart     # lint, render and validate the Helm chart (needs helm and kubeconform)
make eval      # the evaluation set and its quality gate; report in eval/reports/
make up-ollama # the stack on local models served by Ollama
```

The backend tests start real PostgreSQL and Redis containers (testcontainers)
and run the real Alembic migrations, so row-level security is exercised exactly
as in production.

## Design documents

- [Architecture](docs/ARCHITECTURE.md): C4 views, the LangGraph workflow, the
  request sequence, the three isolation checks, ingestion, data model.
- [Security and threat model](docs/SECURITY.md): twelve threats, their
  mitigations, and the test that proves each one.
- [Evaluation](docs/EVALUATION.md): dataset, metric definitions, threshold
  calibration, baseline, the gate, and what the numbers do not prove.
- [Deployment](docs/DEPLOYMENT.md): the pipeline, verifying an image signature, the
  Helm chart, network rules and observability.
- [Runbook](docs/RUNBOOK.md): what to do when each alert fires.
- [Performance](docs/PERFORMANCE.md): the load test, its results, and the bug it found.
- [Architecture Decision Records](docs/adr/README.md): twenty-one decisions, each
  with the options weighed and when to revisit it.

## Stack

Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2 (async) · Alembic · PostgreSQL
with row-level security · Redis · Celery · Next.js 16 · TypeScript · Tailwind ·
Docker · GitHub Actions · cosign · Trivy · CodeQL · LangGraph · pgvector (Pinecone
adapter) · OpenAI adapter · Ollama · SeaweedFS · OpenTelemetry · Prometheus · Grafana ·
Helm · k3s · k6.

## Roadmap

| Phase | Scope | Status |
|-------|-------|--------|
| 0 | Design: architecture, ADRs, threat model | Done |
| 1 | Foundations: auth, tenancy, RLS, compose stack, CI/CD | Done |
| 2 | Ingestion pipeline: upload, parse, chunk, embed, versioning | Done |
| 3 | Retrieval and the LangGraph workflow, SSE streaming, isolation tests | Done |
| 4 | Evaluation: dataset, metrics, threshold calibration, CI gate | Done |
| 5 | Enterprise features: feedback, analytics, audit log viewer, memory, limits | Done |
| 6 | Frontend: chat with citations, document management, dashboard | Done |
| 7 | Production readiness: Helm, tracing, dashboards, load test | Done |

## License

[MIT](LICENSE)
