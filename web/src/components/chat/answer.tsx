"use client";

import { useState } from "react";

import { Badge, cx } from "@/components/ui";
import { aliasOrder, describeSource, reasonLabel, splitMarkers } from "@/lib/citations";
import { request } from "@/lib/client";
import type { Citation, Rating } from "@/lib/types";

export type AssistantTurn = {
  key: string;
  messageId: string | null;
  sentences: string[]; // validated sentences, with their [Sn] markers
  notice: string | null; // a refusal, clarification request or "I don't know"
  citations: Citation[];
  abstained: boolean;
  abstainReason: string | null;
  stage: "retrieving" | "generating" | "done" | "failed";
};

/**
 * One answer. Text is rendered as text, never as HTML or Markdown: an answer
 * quotes documents, and a document must not be able to inject markup, links or
 * images into the page (SECURITY.md T2, T4).
 */
export function Answer({ turn }: { turn: AssistantTurn }) {
  const [active, setActive] = useState<string | null>(null);
  const order = aliasOrder(turn.sentences);
  const byAlias = new Map(turn.citations.map((c) => [c.alias, c]));
  const number = (alias: string) => order.indexOf(alias) + 1;

  if (turn.notice !== null && turn.sentences.length === 0) {
    return (
      <div className="flex flex-col gap-2">
        <div
          className={cx(
            "rounded-xl border px-4 py-3 text-sm",
            turn.abstained
              ? "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-100"
              : "border-slate-200 bg-white text-slate-800 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-100",
          )}
        >
          {turn.abstained && (
            <p className="mb-1 text-xs font-semibold tracking-wide uppercase opacity-80">
              {reasonLabel(turn.abstainReason)}
            </p>
          )}
          <p>{turn.notice}</p>
        </div>
        {turn.messageId && turn.abstained && <Feedback messageId={turn.messageId} />}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div
        className="rounded-xl border border-slate-200 bg-white px-4 py-3 text-[15px] leading-7 text-slate-800 shadow-sm dark:border-slate-800 dark:bg-slate-900 dark:text-slate-100"
        aria-live="polite"
      >
        {turn.sentences.map((sentence, i) => (
          <span key={i}>
            {splitMarkers(sentence).map((part, j) =>
              part.kind === "text" ? (
                <span key={j}>{part.value}</span>
              ) : (
                <button
                  key={j}
                  type="button"
                  onClick={() => setActive(active === part.alias ? null : part.alias)}
                  aria-label={`Source ${number(part.alias)}`}
                  className={cx(
                    "mx-0.5 inline-flex h-5 min-w-5 -translate-y-1 items-center justify-center rounded px-1 align-baseline text-[11px] font-semibold",
                    active === part.alias
                      ? "bg-indigo-600 text-white"
                      : "bg-indigo-50 text-indigo-700 hover:bg-indigo-100 dark:bg-indigo-950 dark:text-indigo-300",
                  )}
                >
                  {number(part.alias)}
                </button>
              ),
            )}{" "}
          </span>
        ))}
        {turn.stage !== "done" && turn.stage !== "failed" && (
          <span aria-hidden className="ml-1 inline-block h-4 w-1.5 animate-pulse bg-indigo-500" />
        )}
      </div>

      {order.length > 0 && (
        <ol className="flex flex-col gap-1.5">
          {order.map((alias) => {
            const source = byAlias.get(alias);
            return (
              <li
                key={alias}
                className={cx(
                  "flex items-start gap-2 rounded-lg border px-3 py-2 text-sm transition",
                  active === alias
                    ? "border-indigo-300 bg-indigo-50 dark:border-indigo-800 dark:bg-indigo-950"
                    : "border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900",
                )}
              >
                <span className="mt-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded bg-slate-100 px-1 text-[11px] font-semibold text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                  {number(alias)}
                </span>
                {source ? (
                  <span className="min-w-0">
                    <span className="font-medium text-slate-900 dark:text-slate-100">
                      {source.document_title}
                    </span>
                    {describeSource(source) && (
                      <span className="block truncate text-xs text-slate-500">
                        {describeSource(source)}
                      </span>
                    )}
                  </span>
                ) : (
                  <span className="text-slate-400">Resolving source…</span>
                )}
              </li>
            );
          })}
        </ol>
      )}
      {turn.messageId && turn.stage === "done" && <Feedback messageId={turn.messageId} />}
    </div>
  );
}

function Feedback({ messageId }: { messageId: string }) {
  const [rating, setRating] = useState<Rating | null>(null);
  const [failed, setFailed] = useState(false);

  async function rate(value: Rating) {
    setRating(value);
    setFailed(false);
    try {
      await request(`/api/v1/messages/${messageId}/feedback`, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ rating: value }),
      });
    } catch {
      setFailed(true);
    }
  }

  return (
    <div className="flex items-center gap-2 text-xs text-slate-500">
      <span>Was this helpful?</span>
      {([1, -1] as const).map((value) => (
        <button
          key={value}
          type="button"
          onClick={() => rate(value)}
          aria-pressed={rating === value}
          aria-label={value === 1 ? "Helpful" : "Not helpful"}
          className={cx(
            "rounded-md border px-2 py-0.5",
            rating === value
              ? "border-indigo-500 bg-indigo-50 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300"
              : "border-slate-200 hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800",
          )}
        >
          {value === 1 ? "Yes" : "No"}
        </button>
      ))}
      {rating !== null && !failed && <Badge tone="success">Thanks</Badge>}
      {failed && <Badge tone="danger">Not saved</Badge>}
    </div>
  );
}
