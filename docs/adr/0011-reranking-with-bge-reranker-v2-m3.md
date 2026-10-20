# 0011. Reranking with `bge-reranker-v2-m3`

- **Status:** Proposed
- **Date:** 2026-10-20

## Context

Dense retrieval is tuned for recall; the top results are often near-duplicates
or topically close but unhelpful. A reranker re-scores the candidate set against
the question before generation, which lets us retrieve wide (`top_k`) and send
few, better chunks (`rerank_top_n`) to the LLM: better answers and fewer tokens.

The specification allows a cross-encoder or an LLM-based reranker.

Facts checked on 2026-10-20 in the Pinecone documentation:

- Pinecone Inference hosts `bge-reranker-v2-m3` (open-source, multilingual,
  scores normalised to 0–1) ([rerank guide](https://docs.pinecone.io/guides/inference/rerank)).
- Since 2026-08-01, requests to `cohere-rerank-3.5` are served by
  `cohere-rerank-4-fast`, **which returns different scores**
  ([model page](https://docs.pinecone.io/models/cohere-rerank-3.5)), so any
  threshold calibrated on it silently changed.

## Options considered

### A. LLM-as-reranker
Flexible, no extra model; slow, costly per candidate, and scores are not stable
across model versions.

### B. Hosted proprietary reranker (Cohere via Pinecone)
Strong quality; scores can change when the vendor swaps the model behind a name,
as the fact above shows.

### C. `bge-reranker-v2-m3` cross-encoder
Open weights: hosted on Pinecone for production, runnable locally with the same
weights for evaluation and the offline profile. Identical model, identical
scores, in both places.

## Decision

Option C behind the `Reranker` port: `PineconeReranker` in production,
`LocalCrossEncoderReranker` (same weights) for evaluation runs, and a
pass-through `FakeReranker` in unit tests. The model identifier is pinned in
configuration and recorded on every message.

Reranking is applied after `grade_retrieval == "good"`; the grade uses vector
similarity scores, so a reranker outage degrades to "no reranking" (logged and
counted) instead of failing the request.

## Consequences

- Phase 4 calibrates `rerank_top_n` and measures the reranker's effect on hit@k
  and MRR, so its cost is justified with numbers.
- The local model adds a heavy optional dependency, installed only in the eval
  extra, not in the API image.

## Revisit when

The evaluation shows no gain over vector order, or a multilingual corpus needs a
different model.
