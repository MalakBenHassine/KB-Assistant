import { apiUrl } from "@/lib/server/api";
import { problem, rejectCrossOrigin, relay } from "@/lib/server/respond";
import { saveTokens, type TokenPair } from "@/lib/server/session";

/** Sign in: the tokens go into HttpOnly cookies and never reach the browser's JavaScript. */
export async function POST(request: Request) {
  const rejected = rejectCrossOrigin(request);
  if (rejected) return rejected;

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return problem(422, "validation_error", "The request is invalid.");
  }
  const headers: Record<string, string> = { "content-type": "application/json" };
  // The ingress appends the client's address; the API audits it, trusting only
  // as many hops as configured (KB_TRUSTED_PROXY_HOPS, DEPLOYMENT.md).
  const forwardedFor = request.headers.get("x-forwarded-for");
  if (forwardedFor) headers["x-forwarded-for"] = forwardedFor;
  const upstream = await fetch(apiUrl("/v1/auth/login"), {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!upstream.ok) return relay(upstream);
  const tokens = (await upstream.json()) as TokenPair;
  await saveTokens(tokens);
  return Response.json(
    { tenant_id: tokens.tenant_id },
    { headers: { "cache-control": "no-store" } },
  );
}
