import "server-only";

/**
 * The shared accounts of the public demo, shown on the sign-in page only when
 * `KB_DEMO_PASSWORD` is set (the Helm chart sets it from the demo seed Secret,
 * and only in the demo values). The API is read-only in the demo, so sharing
 * the password lets visitors try everything without changing anything.
 */
const ACCOUNTS = [
  { email: "carol@acme.example", what: "Employee, HR only" },
  { email: "dave@acme.example", what: "Employee, Engineering and Product" },
  { email: "alice@acme.example", what: "Admin: documents, analytics, audit log" },
  { email: "erin@globex.example", what: "Admin of another company" },
];

export function DemoAccounts() {
  const password = process.env.KB_DEMO_PASSWORD;
  if (!password) return null;
  return (
    <aside
      aria-label="Demo accounts"
      className="rounded-xl border border-indigo-200 bg-indigo-50 p-4 text-sm dark:border-indigo-900 dark:bg-indigo-950"
    >
      <p className="font-medium text-indigo-900 dark:text-indigo-200">
        Public demo, read-only. Password for every account:{" "}
        <code className="rounded bg-white px-1.5 py-0.5 dark:bg-slate-900">{password}</code>
      </p>
      <ul className="mt-2 flex flex-col gap-1 text-indigo-900 dark:text-indigo-200">
        {ACCOUNTS.map((a) => (
          <li key={a.email}>
            <code>{a.email}</code> <span className="opacity-75">· {a.what}</span>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-xs text-indigo-800/80 dark:text-indigo-300/80">
        Compare what each one may read. Conversations are wiped every night.
      </p>
    </aside>
  );
}
