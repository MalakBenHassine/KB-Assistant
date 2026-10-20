# 0008. Next.js as a backend-for-frontend; no token in the browser

- **Status:** Proposed
- **Date:** 2026-10-20

## Context

The API issues JWT access tokens and opaque refresh tokens
([ADR-0005](0005-authentication-and-authorization-model.md)). The frontend is
Next.js (App Router). Where the browser keeps those tokens decides what an XSS
bug costs: a token readable by JavaScript can be exfiltrated and replayed from
anywhere until it expires.

Current guidance for browser-based apps (IETF OAuth 2.0 for Browser-Based
Applications) favours a backend-for-frontend that keeps tokens server-side.

## Options considered

### A. Tokens in `localStorage`, SPA calls the API directly
Simplest; any XSS steals the refresh token. Rejected.

### B. Access token in memory, refresh token in an httpOnly cookie set by the API
Better; but the API now serves cookies cross-site, needs CORS with credentials,
and CSRF protection on the refresh endpoint.

### C. Next.js server as a BFF
Route handlers and server actions call the API with the bearer token; the
browser holds only `__Host-` cookies that JavaScript cannot read. The API never
receives browser traffic and needs no CORS at all.

## Decision

Option C.

- `POST /api/auth/login` (Next.js route handler) calls the API, stores the access
  token and the refresh token in two cookies: `__Host-kb_at` and `__Host-kb_rt`,
  `HttpOnly; Secure; SameSite=Lax; Path=/`. The refresh cookie is sent only to
  the refresh route.
- A server-side fetch wrapper attaches the bearer token, refreshes once on 401
  with single-flight de-duplication, and clears cookies on refresh failure.
- The chat stream: browser → Next.js route handler → API SSE, piped through
  without buffering.
- CSRF: `SameSite=Lax`, an `Origin` check on every state-changing route, and a
  double-submit token for form posts.
- The API is not exposed to browsers in production (ingress routes only the web
  app publicly; the API is reachable from the web pods and, for machine clients,
  through a separate host with its own authentication if ever needed).
- Strict Content-Security-Policy with per-request nonces; no
  `dangerouslySetInnerHTML`; markdown rendered without raw HTML or images
  (SECURITY.md T2, T4).

## Consequences

- An XSS bug can act as the user while the page is open, but cannot steal a
  reusable credential.
- Every API call from the UI takes one extra hop through the Next.js server;
  negligible next to LLM latency.
- The web container is now a security component and is tested as one
  (cookie attributes, origin checks).

## Revisit when

Native mobile clients appear: they use the authorization-code flow with PKCE
against an OIDC provider, not this BFF.
