# 0005. First-party JWT, OIDC-ready, authorization data from the database

- **Status:** Proposed
- **Date:** 2026-10-20

## Context

The specification asks for JWT access and refresh tokens with RBAC
(`employee`, `dept_admin`, `tenant_admin`), and for a design where an OIDC
provider (Keycloak, Auth0) can be plugged in later. Two questions hide inside:

1. **Authentication**: who issues and verifies tokens?
2. **Authorization data**: where do tenant, role and departments come from at
   request time?

The second matters more. If role and departments travel inside the token, a
demoted admin or someone who left HR keeps their access until the token expires,
and a token that is ever mis-issued carries privileges no server check can undo.

## Options considered

### Authentication
- **A1. HS256 shared secret.** Simple, but every verifier holds the signing key,
  and it is a different code path from any OIDC provider (which signs with RS256
  or ES256 and publishes a JWKS).
- **A2. RS256 with a key set (`kid`) and a JWKS endpoint.** The API verifies its
  own tokens exactly as it would verify Keycloak's: fetch keys by `kid`, pin the
  algorithm, check `iss` and `aud`. Swapping the issuer becomes configuration.
- **A3. Use Keycloak from day one.** Strong, but the specification asks for
  register/login/refresh built in, and it adds a heavy container to every dev
  environment.

### Authorization data
- **B1. Role and departments as token claims.** No database lookup; stale for the
  token's lifetime.
- **B2. Token carries identity only (`sub`, `tid`, `ver`); role and departments
  are loaded from Postgres per request.** Always current; one indexed lookup.
  This follows the OWASP guidance to enforce authorization server-side on every
  request rather than trusting client-held state.

## Decision

**A2 + B2.**

- Access token: RS256 JWT, 10-minute lifetime, claims `iss`, `aud`, `sub`,
  `tid`, `ver`, `iat`, `exp`, `jti`. Signing keys identified by `kid`, rotated by
  adding a key, publishing it, switching the signer, then retiring the old key
  after one access-token lifetime. Public keys published at
  `/.well-known/jwks.json`.
- Refresh token: opaque random 256-bit value, **stored hashed** (SHA-256) in
  `refresh_tokens`, rotated on every use, organised in families; presenting an
  already-rotated token revokes the whole family and is audited (stolen-token
  detection). 14-day idle and 30-day absolute expiry.
- Passwords: Argon2id (`argon2-cffi`) with parameters from the OWASP Password
  Storage Cheat Sheet; uniform error and timing for unknown emails.
- Per request: verify the JWT with the algorithm pinned, then load the user by
  `sub` and the membership `(sub, tid)`
  ([ADR-0012](0012-identity-separate-from-tenant-membership.md)); reject if the
  user or the membership is inactive or missing, or if
  `users.token_version != ver`. Role and departments come from the membership
  (cached in-process for a few seconds, keyed by
  `(user_id, tenant_id, token_version, membership.updated_at)`).
- Refresh-token families are bound to one membership: a refresh can never yield
  a token for another tenant; switching tenant is an explicit, audited call.
- Authorization is expressed as policies on use cases (`requires(...)`), never as
  ad-hoc checks in routers. A test walks every route and fails if one has no
  policy.
- No open self-registration into a tenant: users are invited by a
  `tenant_admin`. The seed script creates the demo tenants and users.
- **OIDC later**: a `TokenVerifier` port with two adapters (`LocalJwtVerifier`,
  `OidcVerifier` using the provider's discovery document and JWKS). An external
  identity maps to a local user through `(issuer, subject)`; role and
  departments still come from our database, or are synchronised from IdP groups
  by an explicit, audited job, never read live from a token.

## Consequences

- Demotion, deactivation and password change take effect on the next request
  (`token_version` bump).
- One extra indexed query per request, offset by the short cache.
- Key management is now our job: keys come from a Kubernetes Secret, never from
  the repository, and rotation is documented in the runbook.
- The browser never holds these tokens ([ADR-0008](0008-nextjs-bff-token-handling.md)).

## Revisit when

A customer requires SSO: enable `OidcVerifier` and decide on group
synchronisation. Nothing in the application layer changes.
