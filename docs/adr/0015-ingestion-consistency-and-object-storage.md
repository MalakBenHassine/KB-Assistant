# 0015. Ingestion consistency, and S3-compatible storage without MinIO

- **Status:** Proposed
- **Date:** 2026-10-20

## Context

Ingesting a version touches four systems that share no transaction:
object storage (the original file), the embedding provider, the vector store
and PostgreSQL. Any of them can fail at any step; Celery delivers a job at
least once (ADR-0009), so the same job may also run twice. Requirements from
the specification: re-ingesting the same file is a no-op, a new version
archives the old one without deleting it, and the status lifecycle is
`pending → processing → indexed | failed`, with the error stored.

Separately, the architecture named MinIO as local object storage. Checked on
2026-10-20: MinIO stopped publishing community Docker images in October 2025
and archived its repository in 2026; `minio/minio` no longer pulls from
Docker Hub.

## Options considered

### Consistency
- **A. Distributed transaction / two-phase commit** across Postgres and the
  vector store. Not offered by Pinecone; complex where it exists.
- **B. Vector store first, Postgres as an afterthought.** Readers would see
  vectors whose chunks are not committed, or vectors of a version that later fails.
- **C. Postgres is the source of truth; the vector index is a projection.**
  Order the steps so that every intermediate state is safe for readers, make
  every step idempotent, and let retrieval trust Postgres, not the index.

### Local object storage
- **MinIO**: no longer distributed as an image; building it from source for
  a portfolio's compose file is not a reasonable ask of a reviewer.
- **RustFS**: Apache-2.0, a drop-in for MinIO, reached 1.0 in September 2026,
  a month before this decision. Promising, too new.
- **SeaweedFS**: Apache-2.0, in production use for about a decade, a single
  `server -s3` container. Verified here: signed requests with the right key
  succeed, a wrong key gets `SignatureDoesNotMatch`, anonymous requests get 403.

## Decision

**Option C**, implemented in `application/ingestion.py`:

1. **Claim** in a short transaction: a conditional `UPDATE … WHERE status IN
   (pending, failed-and-retryable) OR (processing AND started before the stale
   threshold) RETURNING id`. Exactly one delivery wins; a worker that died
   mid-job is taken over after `stale_after`.
2. **Work outside any transaction**: read the original, parse, clean, chunk,
   embed, upsert vectors. Vector ids are `{version_id}:{chunk_index}`, so a
   retry overwrites instead of duplicating.
3. **Switch** in one transaction: insert chunk rows, mark the version
   `indexed`, point the document at it, archive the previous version. A
   version that finishes after a newer one is archived instead (no lost update).
4. **Deactivate** the previous version's vectors (one filter update on
   Pinecone). If that fails, the old vectors stay flagged active in the index,
   but retrieval hydrates chunks from Postgres and accepts only the current
   version (ARCHITECTURE §6.3, check 2), so nobody sees them;
   `vectors_deactivated_at` stays empty so the step can be replayed.

Failures are classified once: a corrupt or empty file is final
(`retryable=false`), a provider outage is retried with backoff up to
`max_attempts`, then final. Re-uploading the bytes of the latest usable
version is answered with the existing version and enqueues nothing.

The API enqueues **after** committing the upload. If the broker is down at
that instant, the version stays `pending` and an admin can retry it; a
transactional outbox (enqueue from a table written in the same transaction)
would remove that window and is the planned improvement.

**Local object storage is SeaweedFS 4.48**, in compose and in the test
fixtures. Production uses a managed S3; the adapter speaks plain S3 (path-style
addressing, SigV4), so the choice of local server does not leak into the code.

## Consequences

- The index can temporarily contain vectors that retrieval will ignore; the
  invariant to test is "no unauthorized or stale chunk reaches a prompt", not
  "the index is always exact".
- Every path above has an integration test: re-run, new version, late
  version, corrupt file, empty file, outage then recovery, exhausted retries,
  stale claim, wrong tenant.
- One more container in compose; no change to application code if RustFS or
  another S3 server replaces SeaweedFS later.

## Revisit when

- The enqueue-after-commit window shows up in practice (versions stuck in
  `pending`): add the outbox.
- RustFS has a longer track record, if it proves simpler to operate.
