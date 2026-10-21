import "server-only";

import { isSameOriginRequest } from "@/lib/security";

export function problem(status: number, code: string, detail: string): Response {
  return Response.json(
    { type: `urn:kb-assistant:problem:${code}`, status, detail },
    {
      status,
      headers: { "content-type": "application/problem+json", "cache-control": "no-store" },
    },
  );
}

/** Rejects a state-changing request that does not come from this origin. */
export function rejectCrossOrigin(request: Request): Response | null {
  const h = request.headers;
  const host = h.get("x-forwarded-host") ?? h.get("host");
  if (isSameOriginRequest(request.method, h.get("origin"), host, h.get("x-forwarded-proto"))) {
    return null;
  }
  return problem(403, "cross_origin", "This request must come from the application itself.");
}

// Headers worth passing from the API back to the browser; nothing else leaks.
const FORWARDED = [
  "content-type",
  "content-disposition",
  "retry-after",
  "x-request-id",
  "location",
];

/** The API's response, streamed through (SSE included), with a safe header set. */
export function relay(upstream: Response): Response {
  const headers = new Headers({ "cache-control": "no-store" });
  for (const name of FORWARDED) {
    const value = upstream.headers.get(name);
    if (value) headers.set(name, value);
  }
  if (upstream.headers.get("content-type")?.startsWith("text/event-stream")) {
    headers.set("x-accel-buffering", "no");
  }
  return new Response(upstream.body, { status: upstream.status, headers });
}
