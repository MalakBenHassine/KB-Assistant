import type { NextRequest } from "next/server";

import { isForwardablePath } from "@/lib/security";
import { apiFetch } from "@/lib/server/api";
import { problem, rejectCrossOrigin, relay } from "@/lib/server/respond";

// Request headers the API needs; cookies and any client Authorization stay here.
const FORWARDED = ["accept", "content-type"];

/**
 * The browser's only way to the API (ADR-0008): `/api/v1/...` → `<API>/v1/...`
 * with the user's bearer token. Answers stream through untouched, so server-sent
 * events reach the browser sentence by sentence.
 */
async function forward(request: NextRequest, ctx: RouteContext<"/api/v1/[...path]">) {
  const rejected = rejectCrossOrigin(request);
  if (rejected) return rejected;

  const { path } = await ctx.params;
  if (!isForwardablePath(path)) return problem(404, "not_found", "Not found.");

  const headers: Record<string, string> = {};
  for (const name of FORWARDED) {
    const value = request.headers.get(name);
    if (value) headers[name] = value;
  }
  // Buffered (uploads are capped at the API) so the call can be retried once
  // after a token refresh; a stream could not be replayed.
  const body = ["GET", "HEAD"].includes(request.method) ? null : await request.arrayBuffer();
  const target = `/v1/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`;
  return relay(await apiFetch(target, { method: request.method, headers, body }));
}

export { forward as DELETE, forward as GET, forward as PATCH, forward as POST, forward as PUT };
