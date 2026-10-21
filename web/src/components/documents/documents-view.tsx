"use client";

import { useEffect, useState, type FormEvent } from "react";

import { useSession } from "@/components/session";
import { Badge, Button, Card, ErrorBanner, PageHeader, Spinner } from "@/components/ui";
import { getJson, request } from "@/lib/client";
import type { DocumentSummary, Page, VersionStatus } from "@/lib/types";

const ACCEPT = ".pdf,.docx,.md,.markdown,.html,.htm";
const POLL_MS = 2_000;
const STATUS_TONE: Record<VersionStatus, "neutral" | "info" | "success" | "danger"> = {
  pending: "neutral",
  processing: "info",
  indexed: "success",
  failed: "danger",
};

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function DocumentsView() {
  const { me, readable } = useSession();
  const [documents, setDocuments] = useState<DocumentSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [department, setDepartment] = useState("");

  const manageable = me?.role === "tenant_admin" ? readable : (me?.departments ?? []);
  const names = new Map(readable.map((d) => [d.id, d.name]));

  // Bumped to reload the list: after an upload, a retry, or a polling tick.
  const [revision, setRevision] = useState(0);
  const reload = () => setRevision((r) => r + 1);

  useEffect(() => {
    let active = true;
    getJson<Page<DocumentSummary>>("/api/v1/documents")
      .then((page) => active && setDocuments(page.items))
      .catch((e: unknown) => active && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      active = false;
    };
  }, [revision]);

  // Poll while anything is still being indexed.
  const working = documents?.some((d) =>
    ["pending", "processing"].includes(d.latest_version?.status ?? ""),
  );
  useEffect(() => {
    if (!working) return;
    const timer = setInterval(() => setRevision((r) => r + 1), POLL_MS);
    return () => clearInterval(timer);
  }, [working]);

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const element = event.currentTarget; // React clears currentTarget after the handler returns
    const form = new FormData(element);
    const target = (form.get("department_id") as string) || manageable[0]?.id;
    if (!target) return;
    form.set("department_id", target);
    setUploading(true);
    setError(null);
    try {
      await request("/api/v1/documents", { method: "POST", body: form });
      element.reset();
      reload();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setUploading(false);
    }
  }

  async function retry(documentId: string, versionId: string) {
    setError(null);
    try {
      await request(`/api/v1/documents/${documentId}/versions/${versionId}/retry`, {
        method: "POST",
      });
      reload();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-6 px-6 py-8">
      <PageHeader
        title="Documents"
        subtitle="Upload policies, runbooks and specs. Each file is parsed, chunked and indexed for the departments you manage."
      />

      <Card>
        <form onSubmit={upload} className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-slate-600 dark:text-slate-300">Department</span>
            <select
              name="department_id"
              value={department || manageable[0]?.id || ""}
              onChange={(e) => setDepartment(e.target.value)}
              className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-950"
            >
              {manageable.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-1 flex-col gap-1 text-sm">
            <span className="text-slate-600 dark:text-slate-300">
              File (PDF, DOCX, Markdown, HTML)
            </span>
            <input
              name="file"
              type="file"
              required
              accept={ACCEPT}
              className="rounded-lg border border-dashed border-slate-300 px-3 py-1.5 text-sm file:mr-3 file:rounded-md file:border-0 file:bg-slate-100 file:px-3 file:py-1 dark:border-slate-700 dark:file:bg-slate-800"
            />
          </label>
          <Button type="submit" disabled={uploading || manageable.length === 0}>
            {uploading ? "Uploading…" : "Upload"}
          </Button>
        </form>
      </Card>

      <ErrorBanner message={error} />

      <Card className="p-0">
        {documents === null ? (
          <div className="p-5">
            <Spinner label="Loading documents…" />
          </div>
        ) : documents.length === 0 ? (
          <p className="p-5 text-sm text-slate-500">No documents yet.</p>
        ) : (
          <table className="w-full text-left text-sm">
            <thead className="border-b border-slate-200 text-xs text-slate-500 uppercase dark:border-slate-800">
              <tr>
                <th className="px-5 py-3 font-medium">Document</th>
                <th className="px-5 py-3 font-medium">Department</th>
                <th className="px-5 py-3 font-medium">Status</th>
                <th className="px-5 py-3 font-medium">Chunks</th>
                <th className="px-5 py-3 font-medium" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {documents.map((d) => {
                const v = d.latest_version;
                return (
                  <tr key={d.id}>
                    <td className="px-5 py-3">
                      <p className="font-medium text-slate-900 dark:text-slate-100">{d.title}</p>
                      {v && (
                        <p className="text-xs text-slate-500">
                          v{v.version} · {v.filename} · {size(v.size_bytes)}
                        </p>
                      )}
                    </td>
                    <td className="px-5 py-3 text-slate-600 dark:text-slate-300">
                      {names.get(d.department_id) ?? "—"}
                    </td>
                    <td className="px-5 py-3">
                      <div className="flex flex-wrap items-center gap-1.5">
                        {v && <Badge tone={STATUS_TONE[v.status]}>{v.status}</Badge>}
                        {v?.quarantined_passages ? (
                          <Badge tone="warning">
                            {v.quarantined_passages} passage
                            {v.quarantined_passages > 1 ? "s" : ""} withheld
                          </Badge>
                        ) : null}
                      </div>
                      {v?.error && <p className="mt-1 text-xs text-rose-600">{v.error.message}</p>}
                    </td>
                    <td className="px-5 py-3 text-slate-600 dark:text-slate-300">
                      {v?.chunk_count ?? "—"}
                    </td>
                    <td className="px-5 py-3 text-right">
                      {v?.status === "failed" && v.error?.retryable && (
                        <Button variant="secondary" onClick={() => retry(d.id, v.id)}>
                          Retry
                        </Button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>
      <p className="text-xs text-slate-500">
        &ldquo;Withheld&rdquo; passages read as instructions to the assistant and are kept out of
        search; the original file is unchanged (ADR-0017).
      </p>
    </div>
  );
}
