"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { SessionProvider, useSession } from "@/components/session";
import { cx } from "@/components/ui";
import { hardNavigate, request } from "@/lib/client";
import type { Role } from "@/lib/types";

const NAV: { href: string; label: string; minimum: Role }[] = [
  { href: "/chat", label: "Ask", minimum: "employee" },
  { href: "/documents", label: "Documents", minimum: "dept_admin" },
  { href: "/admin", label: "Administration", minimum: "dept_admin" },
];
const RANK: Record<Role, number> = { employee: 0, dept_admin: 1, tenant_admin: 2 };

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <SessionProvider>
      <div className="flex min-h-screen bg-slate-50 dark:bg-slate-950">
        <Sidebar />
        <main className="flex min-w-0 flex-1 flex-col">
          <MobileBar />
          {children}
        </main>
      </div>
    </SessionProvider>
  );
}

function Sidebar() {
  const pathname = usePathname();
  const { me, conversations } = useSession();

  async function signOut() {
    await request("/api/auth/logout", { method: "POST" }).catch(() => undefined);
    hardNavigate("/login");
  }

  async function switchTenant(tenantId: string) {
    await request("/api/auth/switch-tenant", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ tenant_id: tenantId }),
    });
    hardNavigate("/chat");
  }

  return (
    <aside className="hidden w-72 shrink-0 flex-col border-r border-slate-800 bg-slate-900 text-slate-200 md:flex">
      <div className="flex items-center gap-2 px-5 py-5">
        <span
          aria-hidden
          className="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-500 text-sm font-bold text-white"
        >
          KB
        </span>
        <div className="flex flex-col leading-tight">
          <span className="text-sm font-semibold text-white">KB Assistant</span>
          <span className="text-xs text-slate-400">{me?.tenant.name ?? " "}</span>
        </div>
      </div>

      <nav aria-label="Main" className="flex flex-col gap-1 px-3">
        {NAV.filter((item) => me && RANK[me.role] >= RANK[item.minimum]).map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className={cx(
              "rounded-lg px-3 py-2 text-sm font-medium",
              pathname.startsWith(item.href)
                ? "bg-slate-800 text-white"
                : "text-slate-300 hover:bg-slate-800/60 hover:text-white",
            )}
          >
            {item.label}
          </Link>
        ))}
      </nav>

      <div className="mt-6 flex min-h-0 flex-1 flex-col px-3">
        <div className="flex items-center justify-between px-3 pb-2">
          <h2 className="text-xs font-semibold tracking-wide text-slate-400 uppercase">
            Conversations
          </h2>
          <Link
            href="/chat"
            className="rounded-md px-2 py-1 text-xs text-indigo-300 hover:bg-slate-800"
          >
            + New
          </Link>
        </div>
        <ul className="flex min-h-0 flex-col gap-0.5 overflow-y-auto">
          {conversations.map((c) => (
            <li key={c.id}>
              <Link
                href={`/chat/${c.id}`}
                className={cx(
                  "block truncate rounded-lg px-3 py-1.5 text-sm",
                  pathname === `/chat/${c.id}`
                    ? "bg-slate-800 text-white"
                    : "text-slate-300 hover:bg-slate-800/60",
                )}
              >
                {c.title}
              </Link>
            </li>
          ))}
          {conversations.length === 0 && (
            <li className="px-3 text-sm text-slate-500">No conversations yet.</li>
          )}
        </ul>
      </div>

      <div className="border-t border-slate-800 p-4 text-sm">
        {me && me.other_tenants.length > 0 && (
          <label className="mb-3 flex flex-col gap-1 text-xs text-slate-400">
            Organization
            <select
              className="rounded-md border border-slate-700 bg-slate-800 px-2 py-1.5 text-sm text-slate-100"
              value={me.tenant.id}
              onChange={(e) => switchTenant(e.target.value)}
            >
              {[me.tenant, ...me.other_tenants].map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <div className="flex items-center justify-between gap-2">
          <div className="min-w-0">
            <p className="truncate text-slate-100">{me?.email}</p>
            <p className="text-xs text-slate-400">{me?.role.replace("_", " ")}</p>
          </div>
          <button
            type="button"
            onClick={signOut}
            className="rounded-md px-2 py-1 text-xs text-slate-300 hover:bg-slate-800 hover:text-white"
          >
            Sign out
          </button>
        </div>
      </div>
    </aside>
  );
}

/** Small screens: the sidebar is hidden, so navigation moves to a top bar. */
function MobileBar() {
  const pathname = usePathname();
  const { me } = useSession();
  return (
    <nav
      aria-label="Main"
      className="flex items-center gap-1 overflow-x-auto border-b border-slate-800 bg-slate-900 px-3 py-2 md:hidden"
    >
      <span className="mr-2 text-sm font-semibold text-white">KB</span>
      {NAV.filter((item) => me && RANK[me.role] >= RANK[item.minimum]).map((item) => (
        <Link
          key={item.href}
          href={item.href}
          className={cx(
            "rounded-md px-3 py-1.5 text-sm",
            pathname.startsWith(item.href) ? "bg-slate-800 text-white" : "text-slate-300",
          )}
        >
          {item.label}
        </Link>
      ))}
    </nav>
  );
}
