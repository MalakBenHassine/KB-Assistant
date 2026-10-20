# 0012. Identity separate from tenant membership

- **Status:** Proposed
- **Date:** 2026-10-20

## Context

The specification's data model puts `tenant_id`, `role` and `departments[]` on
the `users` row: one person, one tenant. That is the simplest model, and the
usual reason multi-tenant SaaS products undergo a painful migration later:

- A consultant, an auditor, or an employee of two subsidiaries needs access to
  two tenants. With `users.tenant_id` they need two accounts with the same email,
  which breaks email uniqueness, password reset and SSO mapping.
- Enterprise SSO is configured **per tenant** (each customer brings its own IdP).
  The thing an IdP authenticates is a person; what they may do depends on the
  tenant. Mixing the two in one row couples authentication to authorization.
- Role is not a property of a person; it is a property of a person **in a
  tenant**. Being `tenant_admin` at one customer must grant nothing at another.

## Options considered

### A. `users.tenant_id` (the specification)
Simplest queries. Moving to multi-membership later means splitting the users
table, rewriting every foreign key that meant "a user in a tenant", and
re-issuing every token. Expensive and risky, on the table that guards everything.

### B. Global `users` + `memberships(user_id, tenant_id, role)`
A person has one identity and zero or more memberships. Tenant-scoped data
(conversations, feedback, audit entries) references the membership, or the
`(tenant_id, user_id)` pair. Slightly more joins.

### C. Fully separate user pools per tenant
Strongest separation, but the same person is unrelated across tenants, and
SSO-linked accounts get duplicated.

## Decision

Option B, with the v1 product surface kept small.

- `users(id, email UNIQUE, hashed_password NULL, is_active, token_version)`:
  identity only. `hashed_password` is nullable because an SSO-only user has none.
- `memberships(id, user_id, tenant_id, role, is_active, created_at, updated_at)`,
  unique on `(user_id, tenant_id)`, RLS-protected.
- `membership_departments(membership_id, department_id, tenant_id)` with
  composite foreign keys to `memberships(id, tenant_id)` and
  `departments(id, tenant_id)`, so a membership can never be attached to another
  tenant's department, enforced by the database rather than by code.
- Every access token is scoped to exactly one tenant (`tid`). Login picks the
  membership (the most recently used one, or the only one); switching is an
  explicit, audited call that mints a new token pair. A request can never span
  two tenants.
- v1 UI: no tenant switcher; `GET /v1/me` lists other memberships so the switcher
  is a front-end feature later, not a schema change.
- Seed data includes one user with memberships in both demo tenants, so the
  isolation tests cover the hardest case: a legitimate member of tenant B using a
  valid tenant-A token must still see nothing of B.

## Consequences

- Authorization reads the membership, not the user: one indexed lookup on
  `(user_id, tenant_id)` per request ([ADR-0005](0005-authentication-and-authorization-model.md)).
- Deactivating a person everywhere (`users.is_active = false`, bump
  `token_version`) and removing them from one tenant (`memberships.is_active =
  false`) are different operations, which is exactly what HR offboarding and a
  contract ending at one customer need.
- Tenant admins manage memberships, never users: they cannot change another
  tenant's view of the same person, nor their password.

## Revisit when

A customer requires that its users be invisible to the platform outside its own
tenant (strict user-pool isolation): consider option C for that tenant.
