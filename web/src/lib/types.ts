/** The API's response shapes, as the UI uses them (backend/src/kb_assistant/api/*_schemas.py). */

export type Role = "employee" | "dept_admin" | "tenant_admin";

export type Tenant = { id: string; name: string; slug: string };
export type Department = { id: string; slug: string; name: string };

export type Me = {
  user_id: string;
  email: string;
  membership_id: string;
  role: Role;
  tenant: Tenant;
  departments: Department[];
  other_tenants: Tenant[];
};

export type Page<T> = { items: T[] };

export type Conversation = { id: string; title: string; created_at: string; updated_at: string };

export type Citation = {
  alias: string;
  chunk_id: string;
  document_id: string;
  document_title: string;
  heading_path: string[];
  page_start: number | null;
  page_end: number | null;
};

export type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  citations: Citation[];
  abstained: boolean;
  abstain_reason: string | null;
  grade: string | null;
};

export type Answer = {
  message_id: string;
  answer: string;
  citations: Citation[];
  abstained: boolean;
  abstain_reason: string | null;
  needs_clarification: boolean;
  grade: string | null;
  latency_ms: number;
};

export type VersionStatus = "pending" | "processing" | "indexed" | "failed";

export type DocumentVersion = {
  id: string;
  version: number;
  status: VersionStatus;
  filename: string;
  mime_type: string;
  size_bytes: number;
  chunk_count: number | null;
  quarantined_passages: number | null;
  attempts: number;
  error: { code: string; message: string; retryable: boolean } | null;
  created_at: string;
  indexed_at: string | null;
};

export type DocumentSummary = {
  id: string;
  title: string;
  department_id: string;
  created_at: string;
  latest_version: DocumentVersion | null;
};

export type Analytics = {
  days: number;
  questions: number;
  answered: number;
  abstained: number;
  answer_rate: number | null;
  abstentions_by_reason: Record<string, number>;
  feedback_up: number;
  feedback_down: number;
  satisfaction: number | null;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  input_tokens: number;
  output_tokens: number;
  daily: { day: string; questions: number; abstained: number }[];
  knowledge_gaps: { question: string; times_asked: number; last_asked_at: string }[];
};

export type TenantSettings = {
  monthly_token_budget: number | null;
  budget_is_default: boolean;
  usage: {
    month: string;
    questions: number;
    input_tokens: number;
    output_tokens: number;
    tokens: number;
  };
};

export type AuditEntry = {
  id: string;
  seq: number | null;
  action: string;
  actor_user_id: string | null;
  target: string | null;
  ip: string | null;
  details: Record<string, unknown>;
  created_at: string;
  hash: string | null;
};

export type ChainVerdict = {
  valid: boolean;
  checked: number;
  first_broken_seq: number | null;
  reason: string | null;
  head_hash: string;
};

export type Rating = 1 | -1;
