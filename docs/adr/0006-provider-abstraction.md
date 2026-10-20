# 0006. Providers behind ports, with fakes as first-class adapters

- **Status:** Proposed
- **Date:** 2026-10-20

## Context

The embedding model, the chat model, the reranker and the vector store are the
parts of this system most likely to change (price, quality, a customer's data
residency requirement), and the slowest and least deterministic parts to test.
The specification requires that they can be swapped.

LangChain offers its own abstractions (`Embeddings`, `BaseChatModel`). Using them
directly in use cases would tie the domain to a framework's types and versioning,
and they do not express our constraints (a `RetrievalScope`, token accounting,
timeouts).

## Options considered

### A. Call vendor SDKs directly from use cases
Fastest to write; impossible to test without the network; vendor types leak
everywhere.

### B. Use LangChain interfaces as the ports
Swappable across LangChain integrations; but the domain depends on LangChain,
and its interfaces are wider than what we need.

### C. Our own narrow ports (Python `Protocol`s); adapters may use any SDK
The domain owns the interfaces. Adapters wrap the official SDKs (or a LangChain
integration when that is the better client), add timeouts, retries and
telemetry, and translate errors into domain errors.

## Decision

Option C. Ports in `domain/ports/`:

```python
class Embedder(Protocol):
    model_id: str
    dimension: int
    async def embed_documents(self, texts: Sequence[str]) -> list[Vector]: ...
    async def embed_query(self, text: str) -> Vector: ...

class ChatModel(Protocol):
    model_id: str
    async def complete(self, messages: Sequence[ChatMessage], *, schema: type[T] | None = None,
                       max_tokens: int, timeout_s: float) -> Completion[T]: ...
    def stream(self, messages: Sequence[ChatMessage], *, max_tokens: int,
               timeout_s: float) -> AsyncIterator[StreamChunk]: ...

class Reranker(Protocol):
    model_id: str
    async def rerank(self, query: str, chunks: Sequence[RetrievedChunk], top_n: int) -> list[ScoredChunk]: ...

class VectorStore(Protocol): ...   # see ADR-0002
```

- Every adapter call has a timeout; idempotent calls are retried with `tenacity`
  (exponential backoff with jitter, retry only on 429, 5xx and timeouts); the
  chat model is wrapped in a circuit breaker.
- `Completion` carries token usage, so cost tracking works for any provider.
- Structured outputs (intent classification, grading) use a Pydantic schema; the
  adapter maps it to the provider's structured-output feature, and the result is
  validated again on our side.
- **Fakes are first-class adapters** in `infrastructure/fakes/`: a deterministic
  hashing embedder, a scripted chat model, a pass-through reranker. They power
  unit tests and the offline demo profile (`KB_PROVIDER_PROFILE=offline`).
- Adapters are chosen in one composition root (`bootstrap.py`) from settings;
  nothing else instantiates a client. No global clients.
- Model identifiers come from configuration; every message row records which
  model produced it.

## Consequences

- The application layer has no import from `openai`, `pinecone` or
  `langchain_*`; an import-linter contract in CI enforces it.
- Adding a provider is one adapter plus the contract tests.
- We maintain a small amount of glue that LangChain would have given us.
- LangGraph is still used for orchestration
  ([ADR-0007](0007-langgraph-for-the-question-workflow.md)); its nodes call our
  ports.

## Revisit when

A provider capability we need (tool calling, multimodal input) does not fit the
port: widen the port deliberately, with a new ADR if the change is structural.
