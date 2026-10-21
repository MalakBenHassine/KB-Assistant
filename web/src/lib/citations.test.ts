import { describe, expect, it } from "vitest";

import { aliasOrder, describeSource, reasonLabel, splitMarkers } from "@/lib/citations";
import type { Citation } from "@/lib/types";

describe("splitMarkers", () => {
  it("turns citation markers into references and leaves the text as text", () => {
    expect(splitMarkers("Leave is 25 days [S2][S1].")).toEqual([
      { kind: "text", value: "Leave is 25 days " },
      { kind: "cite", alias: "S2" },
      { kind: "cite", alias: "S1" },
      { kind: "text", value: "." },
    ]);
  });

  it("does not treat markup as anything but text", () => {
    expect(splitMarkers("<img src=x onerror=alert(1)>")).toEqual([
      { kind: "text", value: "<img src=x onerror=alert(1)>" },
    ]);
  });
});

describe("aliasOrder", () => {
  it("numbers sources in order of first use, as the API orders citations", () => {
    expect(aliasOrder(["A [S3].", "B [S1] [S3].", "C [S2]."])).toEqual(["S3", "S1", "S2"]);
  });
});

const citation = (overrides: Partial<Citation> = {}): Citation => ({
  alias: "S1",
  chunk_id: "c",
  document_id: "d",
  document_title: "leave.md",
  heading_path: ["Leave policy", "Parental leave"],
  page_start: null,
  page_end: null,
  ...overrides,
});

describe("describeSource", () => {
  it("joins the heading path and the page range", () => {
    expect(describeSource(citation({ page_start: 3, page_end: 4 }))).toBe(
      "Leave policy › Parental leave · p. 3–4",
    );
    expect(describeSource(citation({ heading_path: [], page_start: 2, page_end: 2 }))).toBe("p. 2");
    expect(describeSource(citation({ heading_path: [] }))).toBe("");
  });
});

describe("reasonLabel", () => {
  it("explains known reasons and falls back for unknown ones", () => {
    expect(reasonLabel("insufficient_evidence")).toMatch(/do not contain/);
    expect(reasonLabel("something_new")).toBe("No answer");
    expect(reasonLabel(null)).toBe("No answer");
  });
});
