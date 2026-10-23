"""Embedding adapters (ADR-0006).

`OpenAIEmbedder` batches within the provider's documented limits (checked
2026-10-20): at most 2,048 inputs and 300,000 tokens per request, 8,192 tokens
per input. Our defaults stay well under them. The SDK's own retries are
disabled so that one policy, ours, governs every external call.

The same adapter serves a local model through Ollama's OpenAI-compatible API.
`ZeroPaddedEmbedder` fits a narrower model to the index width (ADR-0022).

`HashingEmbedder` is a deterministic, offline stand-in: feature hashing of
words and word pairs, L2-normalised. It has real lexical similarity (shared
words score higher) and needs no key, which makes it fit for tests, CI and a
demo without an OpenAI account. It is not a semantic model.
"""

import hashlib
import itertools
import math
import re
from collections.abc import Sequence

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
)

from kb_assistant.domain.documents import EmbeddingBatch
from kb_assistant.domain.errors import IngestionError, ProviderUnavailableError
from kb_assistant.domain.ports import Embedder, Tokenizer
from kb_assistant.infrastructure.resilience import retrying

PROVIDER_MAX_INPUTS = 2_048
PROVIDER_MAX_REQUEST_TOKENS = 300_000
PROVIDER_MAX_INPUT_TOKENS = 8_192
_TRANSIENT = (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError)


class OpenAIEmbedder:
    def __init__(
        self,
        client: AsyncOpenAI,
        *,
        model: str,
        dimension: int,
        tokenizer: Tokenizer,
        max_inputs: int = 256,
        max_request_tokens: int = 250_000,
        attempts: int = 5,
        timeout_s: float = 30.0,
        query_prefix: str = "",
        document_prefix: str = "",
    ) -> None:
        if not 0 < max_inputs <= PROVIDER_MAX_INPUTS:
            raise ValueError(f"max_inputs must be within 1..{PROVIDER_MAX_INPUTS}")
        if not 0 < max_request_tokens <= PROVIDER_MAX_REQUEST_TOKENS:
            raise ValueError(f"max_request_tokens must be within 1..{PROVIDER_MAX_REQUEST_TOKENS}")
        self._client = client
        self._model = model
        self._dimension = dimension
        self._tokenizer = tokenizer
        self._max_inputs = max_inputs
        self._max_request_tokens = max_request_tokens
        self._attempts = attempts
        self._timeout_s = timeout_s
        self._query_prefix = query_prefix
        self._document_prefix = document_prefix

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    def batches(self, texts: Sequence[str]) -> list[list[str]]:
        """Group inputs so each request respects both the count and the token budget."""
        batches: list[list[str]] = []
        current: list[str] = []
        budget = 0
        for text in texts:
            tokens = self._tokenizer.count(text)
            if tokens > PROVIDER_MAX_INPUT_TOKENS:
                text = self._tokenizer.split(text, PROVIDER_MAX_INPUT_TOKENS)[0]
                tokens = PROVIDER_MAX_INPUT_TOKENS
            if current and (
                len(current) >= self._max_inputs or budget + tokens > self._max_request_tokens
            ):
                batches.append(current)
                current, budget = [], 0
            current.append(text)
            budget += tokens
        if current:
            batches.append(current)
        return batches

    async def embed_documents(self, texts: Sequence[str]) -> EmbeddingBatch:
        vectors: list[tuple[float, ...]] = []
        total_tokens = 0
        for batch in self.batches([self._document_prefix + t for t in texts]):
            batch_vectors, tokens = await self._embed(batch)
            vectors.extend(batch_vectors)
            total_tokens += tokens
        return EmbeddingBatch(vectors=tuple(vectors), total_tokens=total_tokens)

    async def embed_query(self, text: str) -> tuple[float, ...]:
        vectors, _ = await self._embed([self._query_prefix + text])
        return vectors[0]

    async def _embed(self, batch: list[str]) -> tuple[list[tuple[float, ...]], int]:
        try:
            async for attempt in retrying(attempts=self._attempts, retry_on=_TRANSIENT):
                with attempt:
                    response = await self._client.embeddings.create(
                        model=self._model,
                        input=batch,
                        dimensions=self._dimension,
                        timeout=self._timeout_s,
                    )
        except _TRANSIENT as exc:
            raise ProviderUnavailableError("embedding_provider", str(exc)) from exc
        except (AuthenticationError, PermissionDeniedError) as exc:
            raise IngestionError(
                "embedding_provider_rejected_credentials",
                "The embedding provider rejected the configured credentials.",
                retryable=False,
            ) from exc
        except BadRequestError as exc:
            raise IngestionError(
                "embedding_provider_rejected_input", str(exc), retryable=False
            ) from exc
        ordered = sorted(response.data, key=lambda item: item.index)
        vectors = [tuple(item.embedding) for item in ordered]
        if any(len(v) != self._dimension for v in vectors):
            raise IngestionError(
                "embedding_dimension_mismatch",
                f"Expected {self._dimension}-dimensional vectors from {self._model}.",
                retryable=False,
            )
        return vectors, response.usage.total_tokens


class ZeroPaddedEmbedder:
    """Pads a narrower model's vectors with zeros to the index width (ADR-0022).

    Appending zeros changes neither dot products nor norms, so cosine similarity,
    and with it the ranking and the calibrated threshold, are exactly the model's.
    """

    def __init__(self, inner: Embedder, dimension: int) -> None:
        if inner.dimension > dimension:
            raise ValueError(f"cannot pad {inner.dimension} dimensions down to {dimension}")
        self._inner = inner
        self._dimension = dimension
        self._padding = (0.0,) * (dimension - inner.dimension)

    @property
    def model_id(self) -> str:
        return self._inner.model_id

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed_documents(self, texts: Sequence[str]) -> EmbeddingBatch:
        batch = await self._inner.embed_documents(texts)
        return EmbeddingBatch(
            vectors=tuple(v + self._padding for v in batch.vectors),
            total_tokens=batch.total_tokens,
        )

    async def embed_query(self, text: str) -> tuple[float, ...]:
        return await self._inner.embed_query(text) + self._padding


_WORD = re.compile(r"\w+", re.UNICODE)


class HashingEmbedder:
    def __init__(self, dimension: int) -> None:
        self._dimension = dimension

    @property
    def model_id(self) -> str:
        return f"local-hashing-{self._dimension}"

    @property
    def dimension(self) -> int:
        return self._dimension

    def _vector(self, text: str) -> tuple[float, ...]:
        words = _WORD.findall(text.lower())
        features = words + [f"{a} {b}" for a, b in itertools.pairwise(words)]
        values = [0.0] * self._dimension
        for feature in features:
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self._dimension
            values[bucket] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in values))
        if norm == 0:
            values[0], norm = 1.0, 1.0
        return tuple(v / norm for v in values)

    async def embed_documents(self, texts: Sequence[str]) -> EmbeddingBatch:
        vectors = tuple(self._vector(t) for t in texts)
        tokens = sum(len(_WORD.findall(t)) for t in texts)
        return EmbeddingBatch(vectors=vectors, total_tokens=tokens)

    async def embed_query(self, text: str) -> tuple[float, ...]:
        return self._vector(text)
