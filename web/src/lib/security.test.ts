import { describe, expect, it } from "vitest";

import {
  contentSecurityPolicy,
  isForwardablePath,
  isSameOriginRequest,
  safeNext,
} from "@/lib/security";

const HOST = "kb.example.com";

describe("isSameOriginRequest (CSRF, ADR-0008)", () => {
  it("lets safe methods through without an Origin", () => {
    expect(isSameOriginRequest("GET", null, HOST)).toBe(true);
  });

  it("accepts a state change from the host the browser addressed", () => {
    expect(isSameOriginRequest("POST", "https://kb.example.com", HOST, "https")).toBe(true);
    // Behind a port mapping: the browser's host includes the public port.
    expect(isSameOriginRequest("POST", "http://localhost:3100", "localhost:3100")).toBe(true);
  });

  it.each([
    ["no Origin header", null, null],
    ["another site", "https://evil.example", null],
    ["a sibling subdomain", "https://evil.kb.example.com", null],
    ["another port", "https://kb.example.com:8443", null],
    ["plain HTTP behind a TLS proxy", "http://kb.example.com", "https"],
    ["a malformed value", "not a url", null],
  ])("rejects a state change from %s", (_label, origin, proto) => {
    expect(isSameOriginRequest("PUT", origin, HOST, proto)).toBe(false);
  });

  it("rejects when the host is unknown", () => {
    expect(isSameOriginRequest("POST", "https://kb.example.com", null)).toBe(false);
  });
});

describe("isForwardablePath", () => {
  it("forwards API resources", () => {
    expect(isForwardablePath(["conversations", "abc", "messages"])).toBe(true);
    expect(isForwardablePath(["admin", "audit-log.csv"])).toBe(true);
  });

  it.each([[[]], [["auth", "refresh"]], [["..", "healthz"]], [["documents", ""]]])(
    "refuses %j",
    (segments) => {
      expect(isForwardablePath(segments)).toBe(false);
    },
  );
});

describe("safeNext (open redirects)", () => {
  it.each([
    ["/documents?x=1", "/documents?x=1"],
    [undefined, "/chat"],
    ["https://evil.example", "/chat"],
    ["//evil.example", "/chat"],
    ["/\\evil.example", "/chat"],
    ["javascript:alert(1)", "/chat"],
  ])("%s → %s", (input, expected) => {
    expect(safeNext(input)).toBe(expected);
  });
});

describe("contentSecurityPolicy (SECURITY.md T4)", () => {
  it("allows only nonce-bearing scripts and no framing in production", () => {
    const csp = contentSecurityPolicy("abc123", false);
    expect(csp).toContain("script-src 'self' 'nonce-abc123' 'strict-dynamic'");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).not.toContain("unsafe-inline");
    expect(csp).not.toContain("unsafe-eval");
  });

  it("allows eval only in development, where React needs it for error overlays", () => {
    expect(contentSecurityPolicy("n", true)).toContain("'unsafe-eval'");
  });
});
