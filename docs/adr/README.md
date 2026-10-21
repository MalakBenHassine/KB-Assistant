# Architecture Decision Records

Each record captures one decision: the context that forced it, the options that
were weighed, what was chosen, and what it costs. Records are **immutable** once
accepted. A changed decision gets a new record that supersedes the old one, so
the history of *why* is never rewritten.

| # | Decision | Status |
|---|----------|--------|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | Accepted |
| [0002](0002-vector-store-pinecone-with-pgvector-for-local.md) | Pinecone in production, pgvector for local development and tests | Proposed |
| [0003](0003-tenant-isolation-namespaces-plus-metadata-filters.md) | Tenant isolation: namespace per tenant **and** metadata filters | Proposed |
| [0004](0004-chunking-strategy.md) | Structure-aware, token-bounded chunking | Proposed |
| [0005](0005-authentication-and-authorization-model.md) | First-party JWT, OIDC-ready, authorization data from the database | Proposed |
| [0006](0006-provider-abstraction.md) | Providers behind ports, with fakes as first-class adapters | Proposed |
| [0007](0007-langgraph-for-the-question-workflow.md) | LangGraph for the question workflow | Proposed |
| [0008](0008-nextjs-bff-token-handling.md) | Next.js as a backend-for-frontend; no token in the browser | Proposed |
| [0009](0009-celery-for-background-jobs.md) | Celery for background jobs (deviation: not arq) | Proposed |
| [0010](0010-hexagonal-architecture-and-repository-layout.md) | Hexagonal architecture and monorepo layout | Proposed |
| [0011](0011-reranking-with-bge-reranker-v2-m3.md) | Reranking with `bge-reranker-v2-m3` | Proposed |
| [0012](0012-identity-separate-from-tenant-membership.md) | Identity separate from tenant membership | Proposed |
| [0013](0013-upload-first-connectors-behind-a-source-port.md) | Upload first; connectors behind a `DocumentSource` port | Proposed |
| [0014](0014-delivery-pipeline-and-deployment-targets.md) | Delivery pipeline: web on Vercel, signed images for the backend | Proposed |
| [0015](0015-ingestion-consistency-and-object-storage.md) | Ingestion consistency; SeaweedFS instead of MinIO | Proposed |
| [0016](0016-gate-merges-on-a-versioned-evaluation-set.md) | Gate merges on a versioned evaluation set run through the real pipeline | Proposed |
| [0017](0017-quarantine-instruction-like-passages-at-ingestion.md) | Quarantine instruction-like passages at ingestion | Proposed |

Template: [`template.md`](template.md).
