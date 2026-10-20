import { connection } from "next/server";

// Liveness probe for the container. `connection()` opts out of prerendering,
// so the answer comes from the running server, not from the build.
export async function GET() {
  await connection();
  return Response.json({ status: "ok" }, { headers: { "Cache-Control": "no-store" } });
}
