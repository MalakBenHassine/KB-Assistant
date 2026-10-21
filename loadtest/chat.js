// Load test of the answering path: login, then concurrent questions from the
// demo users, each within their own departments (make loadtest).
//
// What it measures: the API, Postgres with row-level security, pgvector, the
// LangGraph workflow and the persistence of every answer, with the offline
// models (no provider latency). What it does not: a hosted model's latency,
// which dominates in production and is bounded by its own timeout.
import http from "k6/http";
import { check, fail } from "k6";
import { Counter, Rate, Trend } from "k6/metrics";

const API = __ENV.API_URL || "http://127.0.0.1:8000";
const PASSWORD = __ENV.KB_SEED_PASSWORD;

// Each demo user asks questions their departments can answer (eval/dataset.jsonl).
const USERS = {
  "carol@acme.example": [
    "How many days of paid leave do employees get per year?",
    "What is the home office budget?",
    "How long does the probation period last?",
  ],
  "dave@acme.example": [
    "On which days do production deployments happen?",
    "How much does the Team plan cost?",
    "How often is the NPS survey sent to customers?",
  ],
  "frank@globex.example": [
    "How long are application logs kept?",
    "When do break-glass sessions expire?",
  ],
  "alice@acme.example": ["What is the parking policy at the office?", "Hello"],
};

const answerDuration = new Trend("kb_answer_duration", true); // milliseconds
const answered = new Rate("kb_answered");
const serverErrors = new Rate("kb_server_errors");
const limited = new Counter("kb_rate_limited");

export const options = {
  scenarios: {
    questions: {
      executor: "ramping-vus",
      startVUs: 1,
      stages: [
        { duration: "20s", target: 10 },
        { duration: "60s", target: 25 },
        { duration: "20s", target: 0 },
      ],
      gracefulRampDown: "10s",
    },
  },
  thresholds: {
    kb_server_errors: ["rate<0.01"],
    "kb_answer_duration{kind:question}": ["p(95)<1500"],
    http_req_failed: ["rate<0.01"],
  },
  summaryTrendStats: ["avg", "med", "p(90)", "p(95)", "p(99)", "max"],
};

export function setup() {
  if (!PASSWORD) fail("KB_SEED_PASSWORD is not set");
  const sessions = [];
  for (const [email, questions] of Object.entries(USERS)) {
    const login = http.post(`${API}/v1/auth/login`, JSON.stringify({ email, password: PASSWORD }), {
      headers: { "content-type": "application/json" },
    });
    if (login.status !== 200) fail(`login failed for ${email}: ${login.status}`);
    const token = login.json("access_token");
    const headers = { authorization: `Bearer ${token}`, "content-type": "application/json" };
    const conversation = http.post(`${API}/v1/conversations`, JSON.stringify({ title: "Load test" }), { headers });
    sessions.push({ headers, questions, conversation: conversation.json("id") });
  }
  return { sessions };
}

export default function (data) {
  const session = data.sessions[__VU % data.sessions.length];
  const question = session.questions[__ITER % session.questions.length];
  const response = http.post(
    `${API}/v1/conversations/${session.conversation}/messages`,
    JSON.stringify({ question }),
    { headers: session.headers, tags: { kind: "question" }, responseCallback: http.expectedStatuses(200, 429) },
  );
  serverErrors.add(response.status >= 500);
  if (response.status === 429) {
    limited.add(1);
    return;
  }
  check(response, { "answer returned": (r) => r.status === 200 });
  answerDuration.add(response.timings.duration, { kind: "question" });
  answered.add(response.status === 200 && response.json("abstained") === false);
}
