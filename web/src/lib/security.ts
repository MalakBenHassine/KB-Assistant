/**
 * Request checks shared by the proxy and the route handlers. Pure functions,
 * so they are unit-tested without a server.
 */

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

/**
 * CSRF defence for state-changing requests (ADR-0008): the browser must say the
 * request comes from this site. `SameSite=Lax` cookies already stop most
 * cross-site posts; this closes the rest (same-site subdomains, old browsers).
 *
 * Compared with the host the browser addressed (`X-Forwarded-Host` behind a
 * proxy, else `Host`), not with the server's own URL: behind an ingress or a
 * port mapping the server listens on another address than the one users see.
 */
export function isSameOriginRequest(
  method: string,
  originHeader: string | null,
  host: string | null,
  forwardedProto: string | null = null,
): boolean {
  if (SAFE_METHODS.has(method.toUpperCase())) return true;
  if (!originHeader || !host) return false;
  try {
    const origin = new URL(originHeader);
    if (forwardedProto && origin.protocol !== `${forwardedProto.split(",")[0].trim()}:`) {
      return false;
    }
    return origin.host === host.split(",")[0].trim().toLowerCase();
  } catch {
    return false;
  }
}

/** The Content-Security-Policy for one response (SECURITY.md T4). */
export function contentSecurityPolicy(nonce: string, development: boolean): string {
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${development ? " 'unsafe-eval'" : ""}`,
    `style-src 'self' 'nonce-${nonce}'`,
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    ...(development ? [] : ["upgrade-insecure-requests"]),
  ].join("; ");
}

/**
 * API paths the browser may reach through the BFF. Authentication has its own
 * route handlers (they set cookies); everything else under /v1 passes through.
 */
export function isForwardablePath(segments: string[]): boolean {
  if (segments.length === 0) return false;
  if (segments[0] === "auth") return false;
  return segments.every((s) => s.length > 0 && s !== "." && s !== ".." && !s.includes("/"));
}

/** Where to go after signing in: same-site paths only, never an open redirect. */
export function safeNext(next: string | undefined): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) {
    return "/chat";
  }
  return next;
}
