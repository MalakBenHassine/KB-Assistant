import type { NextConfig } from "next";

// Headers for every response. The Content-Security-Policy is per response
// (it carries a nonce) and is set by `src/proxy.ts` (ADR-0020).
const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
  { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
];

const nextConfig: NextConfig = {
  // A self-contained server for the container image; Vercel ignores this.
  output: "standalone",
  poweredByHeader: false,
  // No Cache Components / partial prerendering: every page is per user, and a
  // nonce-based CSP needs every page rendered per request (ADR-0020).
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
