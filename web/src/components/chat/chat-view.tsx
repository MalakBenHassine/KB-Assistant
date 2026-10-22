"use client";

import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";

import { Answer, type AssistantTurn } from "@/components/chat/answer";
import { useSession } from "@/components/session";
import { Button, ErrorBanner, Spinner, cx } from "@/components/ui";
import { ApiError, getJson, request, sendJson } from "@/lib/client";
import { readEvents } from "@/lib/sse";
import type { Answer as Final, Citation, Conversation, Message, Page } from "@/lib/types";

type Turn =
  { kind: "user"; key: string; text: string } | { kind: "assistant"; turn: AssistantTurn };

const MAX_QUESTION = 2_000;
// One question per demo department, each answered by the demo corpus (eval/dataset.jsonl).
// Asked by someone outside that department, they show the access rules at work.
const SUGGESTIONS = [
  "How many days of paid leave do employees get per year?",
  "On which days do production deployments happen?",
  "What discount does annual billing give?",
];

function fromHistory(messages: Message[]): Turn[] {
  return messages.map((m) =>
    m.role === "user"
      ? { kind: "user", key: m.id, text: m.content }
      : {
          kind: "assistant",
          turn: {
            key: m.id,
            messageId: m.id,
            sentences: m.citations.length ? [m.content] : [],
            notice: m.citations.length ? null : m.content,
            citations: m.citations,
            abstained: m.abstained,
            abstainReason: m.abstain_reason,
            stage: "done",
          },
        },
  );
}

export function ChatView({ conversationId }: { conversationId: string | null }) {
  const { readable, reloadConversations } = useSession();
  const [conversation, setConversation] = useState(conversationId);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(conversationId !== null);
  const [error, setError] = useState<string | null>(null);
  const [scope, setScope] = useState<string[]>([]); // empty: every readable department
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!conversationId) return;
    let active = true;
    getJson<Page<Message>>(`/api/v1/conversations/${conversationId}/messages`)
      .then((page) => active && setTurns(fromHistory(page.items)))
      .catch((e: unknown) => active && setError(e instanceof Error ? e.message : String(e)))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [conversationId]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  function update(key: string, change: (t: AssistantTurn) => AssistantTurn) {
    setTurns((all) =>
      all.map((t) =>
        t.kind === "assistant" && t.turn.key === key ? { ...t, turn: change(t.turn) } : t,
      ),
    );
  }

  async function ensureConversation(): Promise<string> {
    if (conversation) return conversation;
    const created = await sendJson<Conversation>("/api/v1/conversations", "POST", {});
    setConversation(created.id);
    // Update the address without remounting this view (and cutting the stream).
    window.history.replaceState(null, "", `/chat/${created.id}`);
    return created.id;
  }

  async function ask(text: string) {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setError(null);
    setQuestion("");
    const key = crypto.randomUUID();
    setTurns((all) => [
      ...all,
      { kind: "user", key: `${key}-q`, text: trimmed },
      {
        kind: "assistant",
        turn: {
          key,
          messageId: null,
          sentences: [],
          notice: null,
          citations: [],
          abstained: false,
          abstainReason: null,
          stage: "retrieving",
        },
      },
    ]);
    const isNew = conversation === null;
    try {
      const id = await ensureConversation();
      const response = await request(`/api/v1/conversations/${id}/messages`, {
        method: "POST",
        headers: { "content-type": "application/json", accept: "text/event-stream" },
        body: JSON.stringify({
          question: trimmed,
          department_ids: scope.length ? scope : null,
        }),
      });
      if (!response.body) throw new Error("The answer could not be read.");
      for await (const { event, data } of readEvents(response.body)) {
        const payload = JSON.parse(data) as Record<string, unknown>;
        if (event === "status") {
          update(key, (t) => ({ ...t, stage: payload.stage as AssistantTurn["stage"] }));
        } else if (event === "sentence") {
          update(key, (t) => ({ ...t, sentences: [...t.sentences, String(payload.text)] }));
        } else if (event === "message") {
          update(key, (t) => ({ ...t, notice: String(payload.text) }));
        } else if (event === "done") {
          const final = payload as unknown as Final;
          update(key, (t) => ({
            ...t,
            messageId: final.message_id,
            citations: final.citations as Citation[],
            abstained: final.abstained,
            abstainReason: final.abstain_reason,
            notice: t.sentences.length ? null : (t.notice ?? final.answer),
            stage: "done",
          }));
        } else if (event === "error") {
          throw new Error(String(payload.detail ?? "The answer could not be completed."));
        }
      }
      if (isNew) await reloadConversations();
    } catch (e: unknown) {
      update(key, (t) => ({ ...t, stage: "failed" }));
      if (e instanceof ApiError && e.status === 429 && e.retryAfterS) {
        setError(`${e.message} (try again in ${e.retryAfterS} s)`);
      } else {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setBusy(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void ask(question);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void ask(question);
    }
  }

  function toggle(id: string) {
    setScope((current) =>
      current.includes(id) ? current.filter((d) => d !== id) : [...current, id],
    );
  }

  const streaming = turns.at(-1);
  const stage = streaming?.kind === "assistant" && busy ? streaming.turn.stage : null;

  return (
    <div className="flex h-screen flex-col">
      <header className="flex flex-wrap items-center gap-2 border-b border-slate-200 bg-white px-6 py-3 dark:border-slate-800 dark:bg-slate-900">
        <span className="mr-1 text-sm text-slate-500">Search in</span>
        <button
          type="button"
          onClick={() => setScope([])}
          className={cx(
            "rounded-full border px-3 py-1 text-xs font-medium",
            scope.length === 0
              ? "border-indigo-500 bg-indigo-50 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300"
              : "border-slate-300 text-slate-600 dark:border-slate-700 dark:text-slate-300",
          )}
        >
          All my departments
        </button>
        {readable.map((d) => (
          <button
            key={d.id}
            type="button"
            onClick={() => toggle(d.id)}
            aria-pressed={scope.includes(d.id)}
            className={cx(
              "rounded-full border px-3 py-1 text-xs font-medium",
              scope.includes(d.id)
                ? "border-indigo-500 bg-indigo-50 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300"
                : "border-slate-300 text-slate-600 dark:border-slate-700 dark:text-slate-300",
            )}
          >
            {d.name}
          </button>
        ))}
      </header>

      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-3xl flex-col gap-6 px-6 py-8">
          {loading && <Spinner label="Loading the conversation…" />}
          {!loading && turns.length === 0 && (
            <div className="flex flex-col items-center gap-4 py-16 text-center">
              <h1 className="text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-100">
                Ask your organization&apos;s documents
              </h1>
              <p className="max-w-md text-sm text-slate-500">
                Every answer cites its sources and uses only the documents you are allowed to read.
                If they do not contain the answer, you will be told so.
              </p>
              <div className="flex flex-wrap justify-center gap-2">
                {SUGGESTIONS.map((s) => (
                  <Button key={s} variant="secondary" onClick={() => ask(s)}>
                    {s}
                  </Button>
                ))}
              </div>
            </div>
          )}
          {turns.map((t) =>
            t.kind === "user" ? (
              <div key={t.key} className="flex justify-end">
                <p className="max-w-[85%] rounded-2xl rounded-br-sm bg-indigo-600 px-4 py-2.5 text-[15px] whitespace-pre-wrap text-white">
                  {t.text}
                </p>
              </div>
            ) : (
              <Answer key={t.turn.key} turn={t.turn} />
            ),
          )}
          {stage === "retrieving" && <Spinner label="Searching the documents you can access…" />}
          {stage === "generating" && <Spinner label="Writing an answer from the sources…" />}
          <div ref={bottom} />
        </div>
      </div>

      <form
        onSubmit={submit}
        className="border-t border-slate-200 bg-white px-6 py-4 dark:border-slate-800 dark:bg-slate-900"
      >
        <div className="mx-auto flex max-w-3xl flex-col gap-2">
          <ErrorBanner message={error} />
          <div className="flex items-end gap-2">
            <label htmlFor="question" className="sr-only">
              Your question
            </label>
            <textarea
              id="question"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={onKeyDown}
              maxLength={MAX_QUESTION}
              rows={2}
              placeholder="Ask a question about your documents…"
              className="min-h-[52px] flex-1 resize-none rounded-xl border border-slate-300 bg-white px-4 py-3 text-[15px] text-slate-900 focus:border-indigo-500 focus:outline-none dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
            />
            <Button type="submit" disabled={busy || !question.trim()} className="h-[52px] px-5">
              Ask
            </Button>
          </div>
          <p className="text-xs text-slate-400">
            Answers come only from your documents and cite them. Enter to send, Shift+Enter for a
            new line.
          </p>
        </div>
      </form>
    </div>
  );
}
