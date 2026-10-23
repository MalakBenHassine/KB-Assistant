# 0022. Run local models through Ollama, zero-padded to the index width

- **Status:** Proposed
- **Date:** 2026-10-23

## Context

The offline profile (hashing embedder, extractive model) proves the pipeline
but not answer quality: it matches words and quotes sentences. The hosted
models (OpenAI) need a paid key. A real, free measurement needs local models.

Ollama serves local models behind an OpenAI-compatible API, including the
Responses API with JSON-schema output and streaming (checked 2026-10-23 with
Ollama 0.40.6: `output_text` deltas, `response.completed` with usage, and
`text.format` honoured). The adapters of ADR-0006 already speak that API.

The vector index has a fixed width of 1536 (pgvector column, migration 0002;
the Pinecone index). Local embedding models are narrower: `nomic-embed-text`
returns 768 dimensions.

## Options considered

### A. A new provider with its own adapters
Duplicates the retry, batching and circuit-breaker logic for an API that is
already compatible.

### B. The OpenAI adapters with a base URL
One client setting. Nothing to duplicate; the vendor SDK stays in
infrastructure.

### C. A configurable index width
Changing the model would mean a migration, a new Pinecone index and a full
re-ingestion, and the width would have to be read wherever vectors are stored.

### D. Zero-pad the model's vectors to the index width
Appending zeros changes neither dot products nor norms, so cosine similarity,
the ranking and the calibrated threshold are exactly the model's. Costs 768
stored zeros per chunk.

## Decision

- `provider: ollama` for embeddings and for the chat model reuses the OpenAI
  adapters with `base_url` (default `http://host.docker.internal:11434/v1`).
  No key is needed.
- `embeddings.model_dimension` declares a narrower model; `ZeroPaddedEmbedder`
  pads its vectors to `embeddings.dimension`. A model wider than the index is
  refused at startup.
- `embeddings.query_prefix` / `document_prefix` carry the task prefixes some
  models are trained with (`search_query: ` / `search_document: ` for nomic).
- Defaults for the local profile: `nomic-embed-text` and `qwen3:4b` (fits a
  6 GB laptop GPU), `min_score` 0.35 (calibrated, docs/EVALUATION.md §5).
- `KB_EVAL_PROFILE=ollama` evaluates them with the same dataset and gate.

## Consequences

- The quality of a real model is measured without a paid key: retrieval and
  abstention improve on the offline profile (docs/EVALUATION.md §5).
- Switching the embedding model on existing data mixes vector spaces: the
  documents must be ingested again (`make clean`).
- Answers take tens of seconds on a laptop GPU; the local profile is for
  measurement and demonstration, not for load.
- CI does not run it: no GPU, and a non-deterministic model needs a different
  gate (floors with a margin over several runs).

## Revisit when

The index has to hold a model wider than 1536, or several embedding models must
coexist; then the width becomes a property of the index (option C).
