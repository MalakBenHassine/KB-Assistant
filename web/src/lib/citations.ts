/**
 * Citation markers in answers. The API keeps `[S1]`-style aliases in the text
 * (validated server side, ARCHITECTURE §5.4); the UI shows them as numbered
 * references, numbered in order of first use, as the API orders `citations`.
 */
import type { Citation } from "@/lib/types";

const MARKER = /\[(S\d{1,3})\]/g;

export type Part = { kind: "text"; value: string } | { kind: "cite"; alias: string };

export function splitMarkers(text: string): Part[] {
  const parts: Part[] = [];
  let last = 0;
  for (const match of text.matchAll(MARKER)) {
    const start = match.index ?? 0;
    if (start > last) parts.push({ kind: "text", value: text.slice(last, start) });
    parts.push({ kind: "cite", alias: match[1] });
    last = start + match[0].length;
  }
  if (last < text.length) parts.push({ kind: "text", value: text.slice(last) });
  return parts;
}

/** Aliases in order of first appearance across the given texts. */
export function aliasOrder(texts: string[]): string[] {
  const seen: string[] = [];
  for (const text of texts) {
    for (const match of text.matchAll(MARKER)) {
      if (!seen.includes(match[1])) seen.push(match[1]);
    }
  }
  return seen;
}

/** "Leave policy › Parental leave · p. 3–4" */
export function describeSource(c: Citation): string {
  const where = c.heading_path.length ? c.heading_path.join(" › ") : null;
  const pages =
    c.page_start == null
      ? null
      : c.page_end != null && c.page_end !== c.page_start
        ? `p. ${c.page_start}–${c.page_end}`
        : `p. ${c.page_start}`;
  return [where, pages].filter(Boolean).join(" · ");
}

const REASONS: Record<string, string> = {
  no_accessible_documents: "You are not in any department yet",
  no_relevant_documents: "Nothing relevant in your documents",
  weak_evidence: "Not enough evidence, please rephrase",
  insufficient_evidence: "The documents do not contain the answer",
  unsupported_answer: "No answer could be fully supported by sources",
  assistant_unavailable: "The assistant is temporarily unavailable",
  not_a_knowledge_question: "Outside your organization's documents",
};

export function reasonLabel(reason: string | null): string {
  return (reason && REASONS[reason]) || "No answer";
}
