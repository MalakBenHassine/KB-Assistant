# 0019. A per-tenant hash chain makes the audit log tamper-evident

- **Status:** Proposed
- **Date:** 2026-10-21

## Context

The audit log is evidence: who uploaded what, who asked what, who changed a
budget. Since Phase 1 the application role can only `INSERT` and `SELECT` it,
so the API cannot rewrite history. The database owner, a backup restore or a
compromised migration still can, silently (SECURITY.md T11).

## Options considered

### A. Grants only (Phase 1)
Stops the application; says nothing about anyone else.

### B. An external append-only store (WORM bucket, ledger database)
Strong, and another system to run and pay for before the product has users.

### C. A hash chain in the table
Each entry stores a per-tenant sequence number, the previous entry's hash, and
the SHA-256 of the previous hash plus a canonical encoding of its own content.
Deleting an entry leaves a gap; editing one changes its hash; recomputing one
hash breaks the next link.

## Decision

C. Writers for one tenant are serialised with a transaction-scoped advisory
lock, so two requests cannot extend the chain from the same entry. The
canonical encoding is JSON with sorted keys and ISO-8601 timestamps, computed
in the domain (`entry_hash`) and recomputed on read by
`GET /v1/admin/audit-log/verify`, which reports the first broken entry and
returns the **head hash**. Entries written before this change have no hash and
are outside the chain. Tenant admins can page through the log and export it as
CSV (formula-safe: cells starting with `=`, `+`, `-` or `@` are quoted).

## Consequences

- An edit or a deletion anywhere but at the end is detected and located.
- **Removing the newest entries is not detectable from the table alone**: the
  chain just ends earlier. Detecting it requires the head hash to be kept
  somewhere the database owner cannot write (a daily export to WORM storage);
  that is a Phase 7 job, and the verify endpoint already returns the value to
  keep.
- Audit writes for one tenant are serialised; at the current write rate (a
  few per request) this is invisible.

## Revisit when

A tenant needs proof for a third party (then anchor the head hash externally
and sign it), or audit write contention appears in traces.
