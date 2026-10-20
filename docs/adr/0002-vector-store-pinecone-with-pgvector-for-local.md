# 0002. Pinecone in production, pgvector for local development and tests

- **Status:** Proposed
- **Date:** 2026-10-20

## Context

We need approximate nearest-neighbour search over chunk embeddings with
metadata filtering and hard tenant isolation. Constraints:

- Production-like behaviour for the portfolio demo, with no index to operate.
- Tests and CI must run **without network access to a paid service** and
  without secrets: a pull request from a fork has no secrets.
- The specification names Pinecone and asks for an abstraction that allows
  "Pinecone → pgvector for local dev/tests".

Facts from the Pinecone documentation (checked 2026-10-20):

- Serverless indexes; Pinecone recommends **one namespace per tenant** for
  multitenancy; namespaces are created on first upsert.
  ([implement multitenancy](https://docs.pinecone.io/guides/index-data/implement-multitenancy))
- Namespaces per serverless index: Starter 100, Builder 1,000, Standard 100,000.
  Starter is limited to 2 GB storage and AWS `us-east-1`.
  ([object limits](https://docs.pinecone.io/reference/api/database-limits/object-limits))
- 40 KB of metadata per record; filter operators include `$eq`, `$in` (up to
  10,000 values), `$and`.
  ([indexing overview](https://docs.pinecone.io/guides/index-data/indexing-overview))

## Options considered

### A. Pinecone everywhere
Faithful to production, but every test run needs a key and network, tests are
slow and flaky, fork PRs cannot run them, and free-tier quotas are shared with
the demo.

### B. pgvector everywhere
One database, transactional consistency with chunk rows, free. But it ignores
the specification and the operational reality of many companies that run a
managed vector database; it would also hide the eventual-consistency problems
(§Consequences) that a real distributed store creates.

### C. Pinecone in production, pgvector locally, behind one port
Both adapters implement the same `VectorStore` protocol and pass the **same
contract test suite**.

## Decision

Option C.

- `domain.ports.VectorStore` exposes `upsert(records)`,
  `query(vector, scope: RetrievalScope, top_k)`, `set_active(ids, active)` and
  `delete(ids, scope)`. It never accepts a raw filter dictionary.
- `PineconeVectorStore` (production, the hosted demo) and `PgVectorStore`
  (`docker compose` default, integration tests) live in `infrastructure/`.
- `tests/contract/test_vector_store_contract.py` runs against `PgVectorStore`
  on every PR and against Pinecone in a nightly job that has the secret.
- The embedding dimension is part of the index configuration and checked at
  startup against the configured embedding model; a mismatch refuses to start.

## Consequences

- `docker compose up` works with no Pinecone account.
- We must write the contract tests carefully: they are what makes the two
  adapters equivalent, including filter semantics (`$in` on a missing field,
  empty scope).
- Pinecone writes are eventually consistent with Postgres. The design does not
  rely on read-after-write in the vector store: hydration from Postgres is the
  source of truth for "is this chunk active and readable" (ARCHITECTURE §6.3).
- The Starter plan's 100 namespaces cap the hosted demo at 100 tenants, which
  is plenty; production would use a paid plan.

## Revisit when

- A tenant requires on-premises data: pgvector or another self-hosted store
  becomes the production adapter for that tenant.
- Hybrid (dense + sparse/BM25) retrieval is needed: compare Pinecone sparse
  vectors with Postgres full-text search before adding a third store.
