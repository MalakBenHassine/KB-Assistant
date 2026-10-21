# 0020. Render every page per request for a nonce-based CSP; BFF details

- **Status:** Proposed
- **Date:** 2026-10-21
- **Refines:** [0008](0008-nextjs-bff-token-handling.md)

## Context

ADR-0008 chose a backend-for-frontend with tokens in `HttpOnly` cookies and a
strict, nonce-based Content-Security-Policy. Building the chat UI on Next.js
16.4 surfaced three points it left open:

1. The skeleton enabled Cache Components and partial prefetching. The Next.js
   documentation (bundled with 16.4, `guides/content-security-policy.md`)
   states that partial prerendering is incompatible with nonces: a prerendered
   static shell carries no nonce, so its scripts would be blocked.
2. ADR-0008 wanted the refresh cookie "sent only to the refresh route", but the
   `__Host-` prefix requires `Path=/`, and the BFF's pass-through route must be
   able to refresh on a 401.
3. Behind an ingress or a port mapping, the server's own URL is not the origin
   the browser used, so an `Origin` check against it rejects every legitimate
   request (found when the first sign-in through the compose stack failed).

## Options considered

For 1: keep Cache Components and a static CSP with `'unsafe-inline'` (weaker
against XSS, which is the threat the BFF exists for); hash-based CSP (Next.js
inlines per-request data, so hashes change per page); or render every page per
request and keep the nonce.

## Decision

- **No Cache Components, every page dynamic.** `proxy.ts` sets a fresh nonce
  and the CSP on every page response; the root layout calls `connection()` so
  nothing is prerendered. Every page is per user anyway; the cost is server
  rendering on navigation, small next to model latency.
- **Cookies:** access token `__Host-kb_at` (`Path=/`, lifetime of the token);
  refresh token `__Secure-kb_rt` with `Path=/api`, so page navigations never
  carry it; a secret-free `__Host-kb_session` marker lets the proxy redirect
  anonymous visitors without seeing either token. All `HttpOnly`, `Secure`,
  `SameSite=Lax`.
- **Refresh is single-flight per refresh token** in the BFF process: the API
  revokes a session when a rotated refresh token is reused, so two concurrent
  requests must never both refresh.
- **CSRF:** `SameSite=Lax` plus an `Origin` check on every state-changing route,
  compared with the host the browser addressed (`X-Forwarded-Host`, else
  `Host`) and, behind TLS termination, the forwarded scheme. The UI sends JSON
  through `fetch` and has no HTML form posts, so the double-submit token of
  ADR-0008 is not needed.
- **One pass-through route**, `/api/v1/[...path]`, forwards to the API with the
  bearer token, except `/v1/auth/*` (dedicated handlers set the cookies). It
  forwards a fixed set of headers each way and streams bodies, so SSE answers
  arrive sentence by sentence.
- **Rendering:** answers are text. Citation markers become buttons; nothing
  from a document is ever interpreted as HTML or Markdown, and charts are SVG
  attributes, so the CSP needs no `style` attribute allowance.

## Consequences

- An XSS bug cannot load a script without the per-response nonce, and cannot
  read a token.
- No static shell and no CDN caching of pages; acceptable for an authenticated
  application.
- Multi-replica BFF deployments can still race on a refresh across replicas;
  the API's reuse detection then signs the user out (safe, slightly annoying).
  Sticky sessions or a shared lock would fix it if it shows up.
- `scripts/bff-smoke.sh` checks the CSP, the redirect, the cookies, the origin
  check and a streamed answer against the running stack, in CI.

## Revisit when

Next.js supports nonces with partial prerendering, or a page becomes public
and worth caching.
