import "server-only";

import { cookies } from "next/headers";

/**
 * Session cookies (ADR-0008). The browser holds them; JavaScript cannot read them.
 *
 * - access token: `__Host-`, path `/`, lives as long as the token;
 * - refresh token: `__Secure-`, path `/api` only, so page navigations never carry it;
 * - a session marker with no secret in it, path `/`, so `proxy.ts` can tell a
 *   signed-in visitor from an anonymous one without seeing either token.
 *
 * `KB_WEB_INSECURE_COOKIES=true` drops `Secure` (and therefore the prefixes) for
 * plain-HTTP development on a host other than localhost. Never in production.
 */
const insecure = process.env.KB_WEB_INSECURE_COOKIES === "true";

const name = (prefix: "__Host-" | "__Secure-", base: string) =>
  insecure ? base : `${prefix}${base}`;

export const COOKIES = {
  access: name("__Host-", "kb_at"),
  refresh: name("__Secure-", "kb_rt"),
  session: name("__Host-", "kb_session"),
} as const;

const REFRESH_PATH = "/api";
// The API's absolute refresh lifetime (AuthSettings.refresh_absolute_ttl_s).
const REFRESH_MAX_AGE_S = 30 * 24 * 3600;

export type TokenPair = {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  tenant_id: string;
};

const base = { httpOnly: true, secure: !insecure, sameSite: "lax" as const };

export async function saveTokens(tokens: TokenPair): Promise<void> {
  const jar = await cookies();
  jar.set(COOKIES.access, tokens.access_token, {
    ...base,
    path: "/",
    maxAge: tokens.expires_in,
  });
  jar.set(COOKIES.refresh, tokens.refresh_token, {
    ...base,
    path: REFRESH_PATH,
    maxAge: REFRESH_MAX_AGE_S,
  });
  jar.set(COOKIES.session, "1", { ...base, path: "/", maxAge: REFRESH_MAX_AGE_S });
}

export async function clearTokens(): Promise<void> {
  const jar = await cookies();
  jar.set(COOKIES.access, "", { ...base, path: "/", maxAge: 0 });
  jar.set(COOKIES.refresh, "", { ...base, path: REFRESH_PATH, maxAge: 0 });
  jar.set(COOKIES.session, "", { ...base, path: "/", maxAge: 0 });
}

export async function readTokens(): Promise<{ access?: string; refresh?: string }> {
  const jar = await cookies();
  return { access: jar.get(COOKIES.access)?.value, refresh: jar.get(COOKIES.refresh)?.value };
}
