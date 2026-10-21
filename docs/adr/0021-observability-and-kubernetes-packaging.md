# 0021. Observability through ports, and a Helm chart hardened by default

- **Status:** Proposed
- **Date:** 2026-10-21

## Context

Phase 7 makes the system operable: someone other than its author must be able
to deploy it, see how it behaves, and act on an alert. ARCHITECTURE §10 asked
for RED metrics, product metrics (grades, abstentions, tokens, hydration
rejections), traces through the graph, and a Kubernetes deployment (§11).

## Options considered

**Metrics and traces.** Call Prometheus and OpenTelemetry directly from the
use cases (simple; ties the business rules to two vendors' SDKs, against the
import contracts of ADR-0010), or define ports (`Telemetry`, `NodeTracer`)
that the composition root fills, with null implementations for tests and tools.

**Packaging.** Raw manifests with Kustomize (no templating, but every
environment repeats the same edits), or a Helm chart with a values schema.

## Decision

- **Ports.** `Telemetry` (requests, answers, abstentions, tokens, rate-limit
  refusals, ingestion) and `NodeTracer` (a span per graph node) live in the
  domain; the application calls them; Prometheus and OpenTelemetry implement
  them in infrastructure. Use cases default to null implementations, so
  measuring is never a precondition for working.
- **Bounded labels.** Route templates, model ids, enum reasons; never a tenant,
  a user or a raw path. Per-tenant numbers are analytics, in PostgreSQL.
- **Tracing off by default**, on with `OTEL_EXPORTER_OTLP_ENDPOINT`: FastAPI,
  SQLAlchemy, Redis, Celery and graph spans; logs carry the trace id.
- **The worker** runs Prometheus in multiprocess mode: forked children write
  samples to an `emptyDir`, the parent serves them.
- **Helm**, because the schema is the strongest guard we can give an operator:
  it refuses unknown keys, malformed digests and secret-looking keys under
  `config`. The chart is restricted by default: numeric non-root users,
  read-only root filesystems, no capabilities, `RuntimeDefault` seccomp, no
  service-account tokens, default-deny NetworkPolicies with explicit openings,
  PodDisruptionBudgets, HPAs, the API never routed by the ingress.
- **Operations as code:** migrations as a pre-upgrade hook, retention and
  audit-head CronJobs running as the owner role, alerts with a runbook section
  each, and a load test with thresholds.

## Consequences

- Business code depends on two small interfaces, not on two SDKs; tests use
  the null objects or record calls.
- The chart is validated in CI (lint, render in three variants, kubeconform
  against Kubernetes 1.33, schema refusals) and scanned by Trivy; one Trivy
  finding is a documented false positive, ignored for one file only.
- The first load test found a connection held for the whole of every request;
  fixing it doubled throughput and cut p99 from 10 s to 0.8 s
  ([PERFORMANCE.md](../PERFORMANCE.md)).
- Not covered: database and Redis provisioning, backups, and the GitOps
  controller that verifies image signatures at pull time.

## Revisit when

More than one cluster or environment needs different values (add an
environment overlay or a GitOps repository), or a tenant needs per-tenant SLOs
(then a bounded tenant label for the largest tenants only).
