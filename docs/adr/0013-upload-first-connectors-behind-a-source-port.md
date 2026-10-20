# 0013. Upload first; connectors behind a `DocumentSource` port

- **Status:** Proposed
- **Date:** 2026-10-20

## Context

Enterprise knowledge lives in Slack, Notion, Google Drive and Confluence, and
products in this space advertise connectors prominently. A connector is not
just an API client. To respect quality goal 1 (isolation) it must:

1. Authenticate to the source (OAuth, per tenant, with token refresh and revocation).
2. **Mirror the source's permissions**: who may read this page, this channel,
   this file. Permissions change independently of content, so they need their own
   synchronisation, including deletions.
3. Sync incrementally (webhooks or change cursors), handle rate limits, and
   reconcile drift.

A connector that ingests content without faithful permissions turns the
assistant into a way to read documents one is not allowed to open in the source
system: precisely the failure this project exists to prevent.

## Options considered

### A. Build one or two connectors in v1
Impressive in a demo; either the permission model is simplified (a security
regression hidden behind a feature) or v1 slips by weeks.

### B. Upload only, no abstraction
Fastest, but connectors later would require reworking ingestion.

### C. Upload only, behind a source port designed for connectors
v1 ships the upload source; the ingestion pipeline does not know where a
document comes from.

## Decision

Option C.

- `domain.ports.DocumentSource` yields `SourceDocument(external_id, title,
  mime_type, fetch() -> bytes, department, acl: SourceAcl | None, updated_at)`.
- `UploadSource` is the only v1 implementation; the uploader's choice of
  department is the ACL, validated against what they may manage.
- `documents.source_type` and `documents.external_id` (unique per tenant and
  source) are in the v1 schema, so connectors need no migration of existing rows.
- The ingestion use case is source-agnostic: versioning, idempotency by checksum,
  archival and status lifecycle are shared.
- Prerequisite recorded for any future connector: permission mirroring at
  chunk level (`acl_groups` predicate in all three isolation checks, ARCHITECTURE
  §6.3) and a permission-sync job with its own tests. No connector ships without
  them.

## Consequences

- v1 demonstrates the hard part (isolation, versioning, grounding) instead of the
  wide part (integrations).
- The roadmap states connectors as future work with the reason above, which is
  itself a design argument worth showing.

## Revisit when

A real tenant needs a source system, or the core phases (0–7) are complete.
