# 0003. Tenant isolation: namespace per tenant and metadata filters

- **Status:** Proposed
- **Date:** 2026-10-20

## Context

Two isolation requirements with different shapes:

- **Tenant isolation** is absolute. No query, ever, may consider another
  tenant's vectors. A failure here is a reportable data breach.
- **Department isolation** is policy inside a tenant. A user may read several
  departments; a `tenant_admin` reads all of them; the set changes when someone
  changes team.

A single mechanism for both is tempting; it is also a single point of failure.

## Options considered

### A. One index, metadata filter on `tenant_id` and `department`
Simplest. Every query's correctness depends on one filter being built right;
one bug and the search space is every tenant. Deleting a tenant means a
delete-by-filter over a shared index.

### B. One index per tenant
Strong isolation, but indexes are a scarce, billed resource (Starter: 5 per
project) and per-tenant provisioning becomes an operational workflow.

### C. One namespace per tenant + metadata filter for departments and versions
The namespace is a physical partition (Pinecone stores namespaces separately).
A query names exactly one namespace, so the tenant boundary is structural rather
than a predicate. Departments and `is_active` are predicates inside it.
Tenant offboarding is "delete namespace".

## Decision

Option C, plus two further checks that do not depend on the vector store.

1. **Namespace** = `t_{tenant_id}`, derived from `RetrievalScope.tenant_id`
   inside the adapter. Callers cannot pass a namespace.
2. **Metadata filter**, built only by the adapter from the scope:
   `{"$and": [{"department": {"$in": [...]}}, {"is_active": {"$eq": true}}]}`.
   An empty department set short-circuits to "no results" without calling the
   store (a `$in: []` filter has adapter-specific semantics and is never sent).
3. **Hydration** from Postgres repeats tenant, department and active-version
   predicates (ARCHITECTURE §6.3, check 2).
4. **Row-level security** in Postgres as the last net (check 3).

The pgvector adapter mirrors the same semantics: a `tenant_id` column with a
mandatory predicate (and RLS) stands in for the namespace.

Metadata per vector: `tenant_id`, `department`, `document_id`, `version_id`,
`chunk_index`, `page`, `is_active`. `tenant_id` is stored even though the
namespace implies it, so a mis-routed upsert is detectable by a periodic
consistency check.

## Consequences

- Isolation tests assert on the adapter's outbound call (namespace and filter),
  not only on results (SECURITY.md, T1).
- Moving a user between departments needs no re-indexing.
- Moving a **document** between departments requires updating vector metadata;
  that is a versioned operation handled by the worker.
- Namespace count is bounded by the plan (Starter 100); tenant creation checks
  the remaining quota.

## Revisit when

- Per-document ACLs are required: add an `acl_groups` predicate to the same three
  checks.
- Cross-tenant search becomes a feature (it should not).
