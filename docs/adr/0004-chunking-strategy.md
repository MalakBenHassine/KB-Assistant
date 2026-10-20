# 0004. Structure-aware, token-bounded chunking

- **Status:** Proposed
- **Date:** 2026-10-20

## Context

Chunking decides what the retriever can find and what a citation points to.
The corpus is enterprise documentation: HR policies with numbered sections,
runbooks with steps and code blocks, specs with tables, FAQs with one question
per heading. Requirements:

- Respect the embedding model's input limit and keep chunks small enough that
  several fit in the generation context.
- Never split a sentence, and avoid separating a heading from its body.
- Keep **page numbers** (PDF citations say "p. 4") and the **heading path**
  ("Leave policy › Parental leave › Eligibility") for citations and context.
- Deterministic output: the same file always yields the same chunks, which makes
  re-ingestion idempotent and evaluation reproducible.

## Options considered

### A. Fixed-size character windows
Trivial and fast; splits sentences and tables, ignores structure. Rejected.

### B. Recursive splitting on separators, token-bounded
Split on the coarsest boundary that yields chunks under the limit (section →
paragraph → line → sentence), measured in tokens. Well understood, deterministic.

### C. Semantic chunking (split where embedding similarity drops)
Can produce better boundaries, but costs an embedding call per sentence at
ingestion, is non-deterministic across model versions, and is hard to explain.

### D. Late chunking / contextual retrieval (LLM-written context per chunk)
Measurably better retrieval in published results, but adds an LLM call per
chunk. A candidate for Phase 4 if evaluation shows a gap.

## Decision

Option B, made structure-aware, with parameters to be calibrated in Phase 4.

1. **Parse to a common structure**: a list of blocks
   `(text, kind ∈ {heading, paragraph, list, table, code}, level, page)`.
   PDF via text extraction with page tracking; DOCX, Markdown and HTML via their
   heading structure.
2. **Clean**: Unicode NFKC, remove zero-width and bidi control characters
   (also a prompt-injection measure, SECURITY.md T2), collapse whitespace, drop
   repeated headers and footers detected across PDF pages.
3. **Group by section** (heading path), then split recursively inside a section
   with separators paragraph → line → sentence, bounded by
   `chunking.max_tokens` with `chunking.overlap_tokens` of overlap. Tables and
   code blocks are kept whole when they fit, otherwise split on row or line
   boundaries with the table header repeated.
4. **Merge** chunks below `chunking.min_tokens` into their neighbour within the
   same section.
5. **Token counting** with the tokenizer of the configured embedding model
   (`cl100k_base` for `text-embedding-3-small`), injected through the
   `Tokenizer` port.
6. **Embedded text = heading path + chunk text**; displayed text = chunk text.
   The heading path gives short chunks their missing context ("Eligibility" alone
   means nothing).
7. A chunk records `page_start`, `page_end`, `heading_path`, `chunk_index`,
   `token_count`, `text_hash`.

Initial values (placeholders, **to be calibrated** against the eval set in
Phase 4 by comparing 256 / 512 / 800 tokens and 0 / 10 / 20 % overlap on hit@k
and MRR): `max_tokens = 512`, `overlap_tokens = 64`, `min_tokens = 50`.

## Consequences

- Citations can say "Employee Handbook, p. 12, Leave policy › Parental leave".
- Changing the chunker is a re-ingestion: a chunker version is stored on
  `document_versions`, and the eval report records which version produced it.
- Parsers per format are the most fragile code; each gets fixture-based tests
  (`tests/fixtures/documents/`).

## Revisit when

Phase 4 shows that retrieval misses come from chunk boundaries (answer split
across chunks, context missing): evaluate option D first.
