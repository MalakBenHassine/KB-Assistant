import { apiUrl } from "@/lib/server/api";
import { rejectCrossOrigin } from "@/lib/server/respond";
import { clearTokens, readTokens } from "@/lib/server/session";

/** Sign out: revoke the refresh token's session at the API, then drop the cookies. */
export async function POST(request: Request) {
  const rejected = rejectCrossOrigin(request);
  if (rejected) return rejected;

  const { refresh } = await readTokens();
  if (refresh) {
    // Best effort: the cookies go either way.
    await fetch(apiUrl("/v1/auth/logout"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
      cache: "no-store",
    }).catch(() => undefined);
  }
  await clearTokens();
  return new Response(null, { status: 204 });
}
