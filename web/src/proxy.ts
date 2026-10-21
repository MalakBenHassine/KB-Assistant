import { NextResponse, type NextRequest } from "next/server";

import { contentSecurityPolicy } from "@/lib/security";

// Mirrors COOKIES.session in lib/server/session.ts (server-only, not importable here).
const SESSION_COOKIES = ["__Host-kb_session", "kb_session"];
const PUBLIC_PAGES = ["/login"];

/**
 * Runs before every page: a fresh CSP nonce per response (ADR-0020), and an
 * optimistic redirect to /login when there is no session at all. The real
 * check is the API's, on every call; this only avoids rendering a page that
 * would immediately fail.
 */
export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const signedIn = SESSION_COOKIES.some((name) => request.cookies.has(name));
  if (!signedIn && !PUBLIC_PAGES.includes(pathname)) {
    const login = new URL("/login", request.url);
    if (pathname !== "/") login.searchParams.set("next", `${pathname}${search}`);
    return NextResponse.redirect(login);
  }

  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const csp = contentSecurityPolicy(nonce, process.env.NODE_ENV === "development");
  const headers = new Headers(request.headers);
  headers.set("x-nonce", nonce);
  headers.set("Content-Security-Policy", csp);
  const response = NextResponse.next({ request: { headers } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

export const config = {
  // Pages only: not route handlers, static assets or prefetches.
  matcher: [
    {
      source: "/((?!api|_next/static|_next/image|favicon.ico).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
