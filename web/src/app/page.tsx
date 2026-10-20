const milestones = [
  { phase: "0", title: "Design, ADRs and threat model", done: true },
  { phase: "1", title: "Foundations: auth, tenancy, row-level security, CI/CD", done: true },
  { phase: "2", title: "Ingestion pipeline", done: false },
  { phase: "3", title: "Retrieval and the LangGraph workflow", done: false },
  { phase: "4", title: "Evaluation and the CI quality gate", done: false },
  { phase: "5", title: "Enterprise features", done: false },
  { phase: "6", title: "Chat interface", done: false },
  { phase: "7", title: "Production readiness", done: false },
];

export default function Home() {
  return (
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-8 px-6 py-16">
      <header className="flex flex-col gap-3">
        <h1 className="text-3xl font-semibold tracking-tight">KB Assistant</h1>
        <p className="text-zinc-600 dark:text-zinc-400">
          Answers from your company&apos;s documents, with citations, drawn only from what you are
          allowed to read.
        </p>
      </header>
      <section aria-labelledby="roadmap-heading" className="flex flex-col gap-3">
        <h2 id="roadmap-heading" className="text-lg font-medium">
          Roadmap
        </h2>
        <ol className="flex flex-col gap-2">
          {milestones.map((m) => (
            <li key={m.phase} className="flex items-center gap-3">
              <span
                aria-label={m.done ? "done" : "planned"}
                className={`inline-flex h-6 w-6 items-center justify-center rounded-full text-xs ${
                  m.done
                    ? "bg-emerald-600 text-white"
                    : "border border-zinc-300 text-zinc-500 dark:border-zinc-700"
                }`}
              >
                {m.phase}
              </span>
              <span>{m.title}</span>
            </li>
          ))}
        </ol>
      </section>
    </main>
  );
}
