import type { Metadata } from "next";

import { LoginForm } from "@/components/login-form";

export const metadata: Metadata = { title: "Sign in · KB Assistant" };

export default async function LoginPage({ searchParams }: PageProps<"/login">) {
  const { next } = await searchParams;
  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-50 px-6 dark:bg-slate-950">
      <div className="grid w-full max-w-4xl overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm md:grid-cols-2 dark:border-slate-800 dark:bg-slate-900">
        <section className="hidden flex-col justify-between bg-slate-900 p-10 text-slate-200 md:flex">
          <div className="flex items-center gap-2">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-500 text-sm font-bold text-white">
              KB
            </span>
            <span className="font-semibold text-white">KB Assistant</span>
          </div>
          <div className="flex flex-col gap-4">
            <h2 className="text-2xl leading-snug font-semibold text-white">
              Answers from your company&apos;s documents. With sources. Only what you may read.
            </h2>
            <ul className="flex flex-col gap-2 text-sm text-slate-300">
              <li>Every sentence cites the document it comes from.</li>
              <li>No answer when the documents do not say.</li>
              <li>Your department&apos;s access, enforced before the search.</li>
            </ul>
          </div>
          <p className="text-xs text-slate-500">
            Multi-tenant · audited · evaluated on every change
          </p>
        </section>
        <section className="flex flex-col justify-center gap-6 p-10">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-100">
              Sign in
            </h1>
            <p className="mt-1 text-sm text-slate-500">
              Use the account your administrator created.
            </p>
          </div>
          <LoginForm next={typeof next === "string" ? next : undefined} />
        </section>
      </div>
    </main>
  );
}
