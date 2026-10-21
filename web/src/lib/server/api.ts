import "server-only";

import { clearTokens, readTokens, saveTokens, type TokenPair } from "@/lib/server/session";

/** The API's base URL: server-side only, never sent to the browser (ADR-0008). */
export function apiUrl(path: string): string {
  const base = process.env.KB_API_URL;
  if (!base) throw new Error("KB_API_URL is not set");
  return new URL(path, base).toString();
}

// One refresh per refresh token at a time. The API rotates refresh tokens and
// treats the reuse of a rotated one as theft (it revokes the whole session), so
// two concurrent requests must never both try to refresh with the same token.
const inFlight = new Map<string, Promise<TokenPair | null>>();

async function refreshOnce(refreshToken: string): Promise<TokenPair | null> {
  const pending = inFlight.get(refreshToken);
  if (pending) return pending;
  const attempt = (async () => {
    const response = await fetch(apiUrl("/v1/auth/refresh"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
      cache: "no-store",
    });
    return response.ok ? ((await response.json()) as TokenPair) : null;
  })();
  inFlight.set(refreshToken, attempt);
  try {
    return await attempt;
  } finally {
    // Keep the result briefly for requests that read the old cookie meanwhile.
    setTimeout(() => inFlight.delete(refreshToken), 10_000);
  }
}

export type ApiInit = {
  method?: string;
  headers?: Record<string, string>;
  body?: BodyInit | null;
};

/**
 * Call the API as the signed-in user. Refreshes the access token when it is
 * missing or rejected, once, then retries; clears the session if that fails.
 * Must run where cookies can be written: a Route Handler or a Server Function.
 */
export async function apiFetch(path: string, init: ApiInit = {}): Promise<Response> {
  let { access, refresh } = await readTokens();
  if (!access && refresh) {
    const renewed = await refreshOnce(refresh);
    if (renewed) {
      await saveTokens(renewed);
      ({ access_token: access, refresh_token: refresh } = renewed);
    }
  }
  if (!access) return unauthenticated();

  const send = (token: string) =>
    fetch(apiUrl(path), {
      method: init.method ?? "GET",
      headers: { ...init.headers, authorization: `Bearer ${token}` },
      body: init.body,
      cache: "no-store",
    });

  const first = await send(access);
  if (first.status !== 401 || !refresh) return first;
  const renewed = await refreshOnce(refresh);
  if (!renewed) {
    await clearTokens();
    return first;
  }
  await saveTokens(renewed);
  return send(renewed.access_token);
}

function unauthenticated(): Response {
  return Response.json(
    {
      type: "urn:kb-assistant:problem:not_authenticated",
      title: "Unauthorized",
      status: 401,
      detail: "Please sign in.",
    },
    { status: 401, headers: { "content-type": "application/problem+json" } },
  );
}
