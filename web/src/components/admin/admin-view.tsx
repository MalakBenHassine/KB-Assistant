"use client";

import { useEffect, useState, type FormEvent } from "react";

import { useSession } from "@/components/session";
import { Badge, Button, Card, ErrorBanner, PageHeader, Spinner, cx } from "@/components/ui";
import { reasonLabel } from "@/lib/citations";
import { getJson, sendJson } from "@/lib/client";
import type { Analytics, AuditEntry, ChainVerdict, TenantSettings } from "@/lib/types";

const PERIODS = [7, 30, 90] as const;
const AUDIT_PAGE = 25;

const percent = (v: number | null) => (v === null ? "—" : `${Math.round(v * 100)} %`);
const count = (v: number) => new Intl.NumberFormat("en").format(v);
const ms = (v: number | null) => (v === null ? "—" : `${(v / 1000).toFixed(1)} s`);

export function AdminView() {
  const { me } = useSession();
  const [days, setDays] = useState<(typeof PERIODS)[number]>(30);
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    getJson<Analytics>(`/api/v1/admin/analytics?days=${days}`)
      .then((a) => active && setAnalytics(a))
      .catch((e: unknown) => active && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      active = false;
    };
  }, [days]);

  const tenantAdmin = me?.role === "tenant_admin";

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-6 py-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <PageHeader
          title="Administration"
          subtitle={
            tenantAdmin
              ? "Usage, quality and governance for your organization."
              : "Questions that searched the departments you manage."
          }
        />
        <div
          role="group"
          aria-label="Period"
          className="flex gap-1 rounded-lg bg-slate-100 p-1 dark:bg-slate-800"
        >
          {PERIODS.map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => setDays(p)}
              aria-pressed={days === p}
              className={cx(
                "rounded-md px-3 py-1 text-sm",
                days === p
                  ? "bg-white font-medium shadow-sm dark:bg-slate-900"
                  : "text-slate-600 dark:text-slate-300",
              )}
            >
              {p} days
            </button>
          ))}
        </div>
      </div>

      <ErrorBanner message={error} />
      {analytics === null ? <Spinner label="Loading analytics…" /> : <Overview a={analytics} />}
      {tenantAdmin && <BudgetCard />}
      {tenantAdmin && <AuditLog />}
    </div>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card className="flex flex-col gap-1">
      <span className="text-xs font-medium tracking-wide text-slate-500 uppercase">{label}</span>
      <span className="text-2xl font-semibold text-slate-900 dark:text-slate-100">{value}</span>
      {hint && <span className="text-xs text-slate-500">{hint}</span>}
    </Card>
  );
}

function Overview({ a }: { a: Analytics }) {
  const reasons = Object.entries(a.abstentions_by_reason).sort((x, y) => y[1] - x[1]);
  return (
    <>
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat
          label="Questions"
          value={count(a.questions)}
          hint={`${count(a.answered)} answered with sources`}
        />
        <Stat
          label="Answer rate"
          value={percent(a.answer_rate)}
          hint={`${count(a.abstained)} abstentions`}
        />
        <Stat
          label="Satisfaction"
          value={percent(a.satisfaction)}
          hint={`${a.feedback_up} helpful · ${a.feedback_down} not helpful`}
        />
        <Stat
          label="Latency p50 / p95"
          value={`${ms(a.latency_p50_ms)}`}
          hint={`p95 ${ms(a.latency_p95_ms)}`}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <h2 className="mb-3 text-sm font-semibold text-slate-900 dark:text-slate-100">
            Questions per day
          </h2>
          <DailyChart daily={fillDays(a.daily, a.days)} />
        </Card>
        <Card>
          <h2 className="mb-3 text-sm font-semibold text-slate-900 dark:text-slate-100">
            Why the assistant abstained
          </h2>
          {reasons.length === 0 ? (
            <p className="text-sm text-slate-500">No abstentions in this period.</p>
          ) : (
            <ul className="flex flex-col gap-2 text-sm">
              {reasons.map(([reason, n]) => (
                <li key={reason} className="flex items-center justify-between gap-2">
                  <span className="text-slate-700 dark:text-slate-300">{reasonLabel(reason)}</span>
                  <Badge>{n}</Badge>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-4 text-xs text-slate-500">
            Tokens: {count(a.input_tokens)} in · {count(a.output_tokens)} out
          </p>
        </Card>
      </div>

      <Card>
        <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Knowledge gaps</h2>
        <p className="mb-3 text-xs text-slate-500">
          Questions your documents do not answer, most asked first. Each one is a document worth
          writing.
        </p>
        {a.knowledge_gaps.length === 0 ? (
          <p className="text-sm text-slate-500">No unanswered questions in this period.</p>
        ) : (
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-slate-500 uppercase">
              <tr>
                <th className="py-2 font-medium">Question</th>
                <th className="py-2 font-medium">Asked</th>
                <th className="py-2 font-medium">Last asked</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {a.knowledge_gaps.map((g) => (
                <tr key={g.question}>
                  <td className="py-2 pr-4 text-slate-800 dark:text-slate-200">{g.question}</td>
                  <td className="py-2 pr-4">{g.times_asked}</td>
                  <td className="py-2 text-slate-500">
                    {new Date(g.last_asked_at).toLocaleDateString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </>
  );
}

const CHART_W = 640;
const CHART_H = 160;
const MAX_BAR = 24;
const DAY_MS = 86_400_000;

/** One entry per day of the period, zeros included, so the time axis is honest. */
function fillDays(daily: Analytics["daily"], days: number): Analytics["daily"] {
  const known = new Map(daily.map((d) => [d.day, d]));
  const today = Date.UTC(
    new Date().getUTCFullYear(),
    new Date().getUTCMonth(),
    new Date().getUTCDate(),
  );
  return Array.from({ length: days }, (_, i) => {
    const day = new Date(today - (days - 1 - i) * DAY_MS).toISOString().slice(0, 10);
    return known.get(day) ?? { day, questions: 0, abstained: 0 };
  });
}

/** SVG attributes, not inline styles: the CSP allows no `style` attribute. */
function DailyChart({ daily }: { daily: Analytics["daily"] }) {
  if (daily.every((d) => d.questions === 0)) {
    return <p className="text-sm text-slate-500">No questions in this period.</p>;
  }
  const peak = Math.max(...daily.map((d) => d.questions), 1);
  const slot = CHART_W / daily.length;
  const bar = Math.min(MAX_BAR, Math.max(2, slot * 0.7));
  return (
    <svg
      viewBox={`0 0 ${CHART_W} ${CHART_H + 20}`}
      role="img"
      aria-label={`Questions per day, peak ${peak}`}
      className="h-48 w-full"
    >
      {daily.map((d, i) => {
        const h = (d.questions / peak) * CHART_H;
        const ha = (d.abstained / peak) * CHART_H;
        const x = i * slot + (slot - bar) / 2;
        return (
          <g key={d.day}>
            <title>{`${d.day}: ${d.questions} questions, ${d.abstained} abstained`}</title>
            <rect x={x} y={CHART_H - h} width={bar} height={h} rx={2} className="fill-indigo-500" />
            <rect
              x={x}
              y={CHART_H - ha}
              width={bar}
              height={ha}
              rx={2}
              className="fill-amber-400"
            />
          </g>
        );
      })}
      <text x={0} y={CHART_H + 16} className="fill-slate-400 text-[11px]">
        {daily[0].day}
      </text>
      <text x={CHART_W} y={CHART_H + 16} textAnchor="end" className="fill-slate-400 text-[11px]">
        {daily[daily.length - 1].day}
      </text>
    </svg>
  );
}

function BudgetCard() {
  const [settings, setSettings] = useState<TenantSettings | null>(null);
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    getJson<TenantSettings>("/api/v1/admin/settings")
      .then((s) => {
        setSettings(s);
        setValue(s.monthly_token_budget === null ? "" : String(s.monthly_token_budget));
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  async function save(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSaved(false);
    try {
      const budget = value.trim() === "" ? null : Number(value);
      setSettings(
        await sendJson<TenantSettings>("/api/v1/admin/settings", "PUT", {
          monthly_token_budget: budget,
        }),
      );
      setSaved(true);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const used = settings?.usage.tokens ?? 0;
  const budget = settings?.monthly_token_budget ?? null;

  return (
    <Card>
      <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
        Monthly token budget
      </h2>
      <p className="mb-3 text-xs text-slate-500">
        Questions stop with a clear message once the month&apos;s tokens are spent (ADR-0018). Leave
        empty for no budget.
      </p>
      {settings && (
        <p className="mb-3 text-sm text-slate-700 dark:text-slate-300">
          {count(settings.usage.questions)} questions and {count(used)} tokens this month
          {budget !== null &&
            ` — ${budget === 0 ? "budget 0" : percent(used / budget) + " of the budget"}`}
          {settings.budget_is_default && <Badge>default</Badge>}
        </p>
      )}
      <form onSubmit={save} className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-slate-600 dark:text-slate-300">Tokens per month</span>
          <input
            inputMode="numeric"
            pattern="[0-9]*"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="No budget"
            className="w-48 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-950"
          />
        </label>
        <Button type="submit">Save</Button>
        {saved && <Badge tone="success">Saved and audited</Badge>}
      </form>
      <div className="mt-3">
        <ErrorBanner message={error} />
      </div>
    </Card>
  );
}

function fetchAuditPage(after: string | null) {
  const query = new URLSearchParams({ limit: String(AUDIT_PAGE) });
  if (after) query.set("cursor", after);
  return getJson<{ items: AuditEntry[]; next_cursor: string | null }>(
    `/api/v1/admin/audit-log?${query}`,
  );
}

function AuditLog() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [verdict, setVerdict] = useState<ChainVerdict | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load(after: string | null) {
    try {
      const page = await fetchAuditPage(after);
      setEntries((current) => (after ? [...current, ...page.items] : page.items));
      setCursor(page.next_cursor);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    let active = true;
    fetchAuditPage(null)
      .then((page) => {
        if (!active) return;
        setEntries(page.items);
        setCursor(page.next_cursor);
      })
      .catch((e: unknown) => active && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      active = false;
    };
  }, []);

  async function verify() {
    setChecking(true);
    setError(null);
    try {
      setVerdict(await getJson<ChainVerdict>("/api/v1/admin/audit-log/verify"));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setChecking(false);
    }
  }

  return (
    <Card className="p-0">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 p-5 dark:border-slate-800">
        <div>
          <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Audit log</h2>
          <p className="text-xs text-slate-500">
            Append-only and hash-chained: verification finds an edited or deleted entry (ADR-0019).
          </p>
        </div>
        <div className="flex items-center gap-2">
          {verdict &&
            (verdict.valid ? (
              <Badge tone="success">Chain intact · {verdict.checked} entries</Badge>
            ) : (
              <Badge tone="danger">
                Broken at #{verdict.first_broken_seq}: {verdict.reason}
              </Badge>
            ))}
          <Button variant="secondary" onClick={verify} disabled={checking}>
            {checking ? "Verifying…" : "Verify integrity"}
          </Button>
          <a
            href="/api/v1/admin/audit-log.csv"
            className="rounded-lg border border-slate-300 px-3.5 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
          >
            Export CSV
          </a>
        </div>
      </div>
      <div className="p-5">
        <ErrorBanner message={error} />
      </div>
      <table className="w-full text-left text-sm">
        <thead className="text-xs text-slate-500 uppercase">
          <tr>
            <th className="px-5 py-2 font-medium">#</th>
            <th className="px-5 py-2 font-medium">When</th>
            <th className="px-5 py-2 font-medium">Action</th>
            <th className="px-5 py-2 font-medium">Target</th>
            <th className="px-5 py-2 font-medium">Hash</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
          {entries.map((e) => (
            <tr key={e.id}>
              <td className="px-5 py-2 text-slate-500">{e.seq ?? "—"}</td>
              <td className="px-5 py-2 whitespace-nowrap text-slate-600 dark:text-slate-300">
                {new Date(e.created_at).toLocaleString()}
              </td>
              <td className="px-5 py-2">
                <code className="text-xs">{e.action}</code>
              </td>
              <td className="max-w-56 truncate px-5 py-2 text-slate-500">{e.target ?? "—"}</td>
              <td className="px-5 py-2 font-mono text-xs text-slate-400">
                {e.hash?.slice(0, 12) ?? "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {cursor && (
        <div className="p-4 text-center">
          <Button variant="ghost" onClick={() => load(cursor)}>
            Load more
          </Button>
        </div>
      )}
    </Card>
  );
}
