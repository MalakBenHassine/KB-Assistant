# 0016. Gate merges on a versioned evaluation set run through the real pipeline

- **Status:** Proposed
- **Date:** 2026-10-21

## Context

The assistant's main promises (answers grounded in the asker's documents,
abstention when they are not enough, no leak across departments or tenants)
are statistical as much as logical. Unit and integration tests prove the
mechanisms; they cannot say whether a new prompt, chunker or threshold makes
answers worse. `retrieval.min_score` was a placeholder (ARCHITECTURE §5.7), and
a threshold has no meaning outside the embedding model that produced the
scores.

## Options considered

### A. Hosted evaluation service or framework (Ragas, LangSmith, ...)
Rich metrics and dashboards, but needs network access and usually a paid model
as a judge on every run, so CI would depend on a key and on another vendor's
uptime, and the numbers would move from run to run.

### B. A notebook run by hand
Cheap, and forgotten after the second week: nothing stops a regression.

### C. An in-repo harness on the real pipeline, gated in CI
The dataset and corpus are versioned with the code; the harness drives the same
graph the API runs, against real PostgreSQL with row-level security and real
pgvector; metrics are plain functions with unit tests; an offline profile
(hashing embedder, extractive model) makes the gate deterministic and free.

## Decision

C. `eval/dataset.jsonl` names, for each question, the asker (tenant, role,
departments) and the expected behaviour. `make eval` runs a probe pass (no
threshold) to calibrate `min_score` and a gate pass (deployed configuration)
whose metrics are checked against `eval/gate.toml`. Isolation limits (leaked
chunks, wrong-tenant facts) are zero for every profile; quality floors are the
recorded baseline minus about one question. The offline gate runs on every
pull request; an `openai` profile runs the same harness with hosted models
when a key is present. Details and baseline: [EVALUATION.md](../EVALUATION.md).

## Consequences

- A change that lowers retrieval, answer or abstention quality, or that lets a
  foreign chunk reach the workflow, fails CI with a report saying which
  question regressed.
- `min_score` is now a measured value (0.07 for the offline embedder) and the
  gate rejects a configured value outside its calibrated interval.
- The offline numbers measure the pipeline, not a language model: the
  extractive stand-in cannot paraphrase or understand. Production quality needs
  an `openai` baseline, which needs a key and costs money per run, so it is run
  on demand, not on every pull request.
- The corpus is small and synthetic; it cannot tune chunk size or `top_k`.

## Revisit when

A hosted-model baseline exists (add its floors and consider an LLM-as-judge
faithfulness metric), or the corpus grows enough that the offline gate takes
more than a few minutes.
