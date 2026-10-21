/** Browser-side calls to the BFF (`/api/...`). Never to the API directly (ADR-0008). */

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly retryAfterS: number | null = null,
  ) {
    super(message);
  }
}

const FALLBACK: Record<number, string> = {
  401: "Your session has ended. Please sign in again.",
  403: "You are not allowed to do this.",
  404: "Not found.",
  429: "Too many requests. Please wait a moment.",
};

/** Turns an RFC 7807 problem response into an `ApiError` with a readable message. */
export async function toError(response: Response): Promise<ApiError> {
  let code = "error";
  let detail = FALLBACK[response.status] ?? "Something went wrong. Please try again.";
  try {
    const body = (await response.json()) as { type?: string; detail?: string };
    code = body.type?.split(":").pop() ?? code;
    if (body.detail) detail = body.detail;
  } catch {
    // Not JSON: keep the fallback.
  }
  const retry = response.headers.get("retry-after");
  return new ApiError(response.status, code, detail, retry ? Number(retry) : null);
}

/**
 * A full page load, on purpose: after signing in or out, or switching
 * organization, nothing held in memory for the previous session may survive.
 */
export function hardNavigate(path: string): void {
  window.location.assign(new URL(path, window.location.origin).toString());
}

function onUnauthenticated(status: number): void {
  if (status === 401 && typeof window !== "undefined" && window.location.pathname !== "/login") {
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    hardNavigate(`/login?next=${next}`);
  }
}

export async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const response = await fetch(path, { ...init, credentials: "same-origin", cache: "no-store" });
  if (!response.ok) {
    onUnauthenticated(response.status);
    throw await toError(response);
  }
  return response;
}

export async function getJson<T>(path: string): Promise<T> {
  return (await (await request(path)).json()) as T;
}

export async function sendJson<T>(path: string, method: string, body: unknown): Promise<T> {
  const response = await request(path, {
    method,
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}
