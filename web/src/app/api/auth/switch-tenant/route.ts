import { apiFetch } from "@/lib/server/api";
import { problem, rejectCrossOrigin, relay } from "@/lib/server/respond";
import { saveTokens, type TokenPair } from "@/lib/server/session";

/** Switch to another organization of the same person: a new token pair for that tenant. */
export async function POST(request: Request) {
  const rejected = rejectCrossOrigin(request);
  if (rejected) return rejected;

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return problem(422, "validation_error", "The request is invalid.");
  }
  const upstream = await apiFetch("/v1/auth/switch-tenant", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!upstream.ok) return relay(upstream);
  const tokens = (await upstream.json()) as TokenPair;
  await saveTokens(tokens);
  return Response.json(
    { tenant_id: tokens.tenant_id },
    { headers: { "cache-control": "no-store" } },
  );
}
