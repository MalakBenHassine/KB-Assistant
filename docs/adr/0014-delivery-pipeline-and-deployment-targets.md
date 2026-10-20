# 0014. Delivery pipeline and deployment targets

- **Status:** Proposed
- **Date:** 2026-10-20

## Context

The project needs continuous delivery, not only continuous integration, and the
question "can we deploy it on Vercel?" has to be answered per component:

| Component | Runtime shape |
|-----------|---------------|
| Web (Next.js, BFF) | Request/response, stateless: what Vercel is built for. |
| API (FastAPI) | Long-lived SSE streams (Phase 3), connection pools to Postgres and Redis, private networking. |
| Worker (Celery) | A process that runs continuously and consumes a queue. |
| PostgreSQL, Redis | Stateful services. |

Vercel runs functions with bounded execution time; it does not host a
long-running queue consumer, and functions reach databases over the public
internet. The constraints also include cost (portfolio budget: free tiers), and
an existing **Oracle Cloud Always Free** Ampere VM (4 ARM cores, 24 GB of RAM)
that already hosts another project with room to spare.

Supply-chain facts that shaped the pipeline (checked 2026-10-20): in March 2026,
76 of 77 tags of `aquasecurity/trivy-action` were force-pushed to
credential-stealing code (CVE-2026-33634); only an immutable release was spared.

## Options considered

### A. Everything on Vercel
Not possible for the worker; SSE and pooled connections fight the function model.

### B. Everything on one container host (VM or PaaS)
Uniform, but gives up what Vercel does best for a Next.js app (CDN, per-commit
deployments) and puts the web tier on the same machine as the data.

### C. Web on Vercel; API, worker and data as signed container images on our own host
Each component runs where its shape fits. The API becomes internet-reachable
(the BFF on Vercel must call it), which changes one assumption of ADR-0008.

## Decision

Option C.

**Pipeline**

1. **CI** (`ci.yml`, every PR and push to main): format, lint, `mypy --strict`,
   import contracts, tests against real Postgres and Redis, coverage gate on
   `domain/` and `application/`, web lint/types/build, production `npm audit`,
   image builds with a Trivy scan, and a `docker compose` smoke test that runs
   `make up`, seeds, logs in and calls the API.
2. **Security** (`security.yml`, PRs, main, weekly): gitleaks over the full
   history, Trivy on lockfiles and IaC, dependency review, CodeQL (Python,
   TypeScript, and the workflows themselves).
3. **CD** (`cd.yml`) triggers on `workflow_run` of CI **on main, only if it
   succeeded**:
   - backend and web images built for `linux/amd64` and `linux/arm64` (the
     target VM is ARM), with SBOM and SLSA provenance attestations, pushed to
     GHCR, tagged with the full commit SHA, and **signed with cosign keyless**
     through GitHub's OIDC token: there is no signing key to store or leak;
   - the web app deployed to Vercel by the workflow with a pinned CLI
     (`vercel pull/build/deploy --prebuilt`). Vercel's own Git deployments are
     disabled (`web/vercel.json`), otherwise Vercel would ship commits that CI
     has not approved.

**Supply-chain rules**

- Every third-party action is pinned by full commit SHA, with the version in a
  comment for Dependabot. A tag is a mutable pointer; the Trivy incident above
  is what happens when you trust one.
- Workflows default to `permissions: contents: read`; jobs request more
  explicitly (`packages: write`, `id-token: write` only where they publish).
- `actions/checkout` runs with `persist-credentials: false`.
- New versions wait: Dependabot has a 7-day cooldown, and tools pinned by hand
  (the Vercel CLI) use a release at least a few weeks old.

**Backend target**

The API, the worker, Postgres and Redis go to the Oracle Always Free VM, as
k3s workloads managed by a GitOps controller pulling the signed images (Phase 7:
Helm chart, admission policy that rejects unsigned images). Until then, the
images are published and verifiable, and the web app on Vercel shows the
static roadmap page: it has no backend to call yet.

**Amendment to ADR-0008.** The API will be reachable from the internet so the
Vercel-hosted BFF can call it. Browsers still never hold a token and the API
sends no CORS headers, so a browser page on another origin cannot read its
responses. The BFF will authenticate to the API's auth endpoints with a client
credential (Phase 6), and those endpoints get rate limiting (Phase 5).

## Consequences

- A merge to main produces, without human steps, signed multi-arch images and a
  production web deployment, or nothing at all if any check fails.
- The deploy job is skipped (not failed) until the Vercel project and its
  `VERCEL_TOKEN` secret exist; `docs/DEPLOYMENT.md` lists the one-time setup.
- Two hosting providers to operate; the web tier holds no data, so a Vercel
  outage degrades the UI only.

## Revisit when

- The BFF's latency to the API (Vercel region → Oracle region) is measurable in
  the Phase 7 load test: if it dominates, co-locate the web tier with the API.
- The project needs preview environments with a backend per pull request.
