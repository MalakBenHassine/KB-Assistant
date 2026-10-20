import math
from types import SimpleNamespace
from typing import Any

import httpx2 as httpx  # the HTTP client of openai 3.x
import pytest
from openai import APITimeoutError, AuthenticationError

from kb_assistant.domain.errors import IngestionError, ProviderUnavailableError
from kb_assistant.infrastructure.embeddings import HashingEmbedder, OpenAIEmbedder
from tests.fakes import WordTokenizer


def cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


async def test_hashing_embedder_is_deterministic_normalised_and_lexical() -> None:
    embedder = HashingEmbedder(256)
    batch = await embedder.embed_documents(
        ["annual leave is 25 days", "annual leave is 25 days", "kubernetes failover runbook"]
    )
    a, b, c = batch.vectors
    assert a == b
    assert math.isclose(math.sqrt(sum(v * v for v in a)), 1.0)
    query = await embedder.embed_query("how many days of annual leave")
    assert cosine(query, a) > cosine(query, c)


async def test_hashing_embedder_handles_empty_text() -> None:
    vector = await HashingEmbedder(8).embed_query("")
    assert math.isclose(sum(v * v for v in vector), 1.0)


class _FakeEmbeddings:
    def __init__(self, behaviour: list[Any]) -> None:
        self.behaviour = behaviour
        self.inputs: list[list[str]] = []

    async def create(self, *, input: list[str], **_: Any) -> Any:
        self.inputs.append(input)
        outcome = self.behaviour.pop(0) if self.behaviour else None
        if isinstance(outcome, Exception):
            raise outcome
        # Returned out of order on purpose: the adapter must sort by index.
        data = [
            SimpleNamespace(index=i, embedding=[float(len(t)), 0.0])
            for i, t in reversed(list(enumerate(input)))
        ]
        return SimpleNamespace(data=data, usage=SimpleNamespace(total_tokens=len(input) * 3))


def embedder(fake: _FakeEmbeddings, **kwargs: Any) -> OpenAIEmbedder:
    client: Any = SimpleNamespace(embeddings=fake)
    return OpenAIEmbedder(client, model="m", dimension=2, tokenizer=WordTokenizer(), **kwargs)


async def test_batches_respect_input_and_token_budgets() -> None:
    fake = _FakeEmbeddings([])
    texts = ["one two three"] * 10  # 3 tokens each
    batch = await embedder(fake, max_inputs=4, max_request_tokens=9).embed_documents(texts)
    assert [len(i) for i in fake.inputs] == [3, 3, 3, 1]
    assert len(batch.vectors) == 10
    assert batch.total_tokens == 30


async def test_vectors_come_back_in_input_order() -> None:
    fake = _FakeEmbeddings([])
    batch = await embedder(fake).embed_documents(["a", "bbb", "cc"])
    assert [v[0] for v in batch.vectors] == [1.0, 3.0, 2.0]


async def test_transient_errors_are_retried_then_succeed() -> None:
    request = httpx.Request("POST", "https://api.openai.example/v1/embeddings")
    fake = _FakeEmbeddings([APITimeoutError(request)])
    batch = await embedder(fake, attempts=2).embed_documents(["x"])
    assert len(fake.inputs) == 2
    assert len(batch.vectors) == 1


async def test_persistent_outage_becomes_a_retryable_ingestion_error() -> None:
    request = httpx.Request("POST", "https://api.openai.example/v1/embeddings")
    fake = _FakeEmbeddings([APITimeoutError(request), APITimeoutError(request)])
    with pytest.raises(ProviderUnavailableError) as raised:
        await embedder(fake, attempts=2).embed_documents(["x"])
    assert raised.value.retryable


async def test_bad_credentials_are_not_retried() -> None:
    request = httpx.Request("POST", "https://api.openai.example/v1/embeddings")
    response = httpx.Response(401, request=request)
    fake = _FakeEmbeddings([AuthenticationError("bad key", response=response, body=None)])
    with pytest.raises(IngestionError) as raised:
        await embedder(fake, attempts=3).embed_documents(["x"])
    assert not raised.value.retryable
    assert len(fake.inputs) == 1


def test_limits_beyond_the_provider_are_refused() -> None:
    with pytest.raises(ValueError, match="max_inputs"):
        embedder(_FakeEmbeddings([]), max_inputs=5_000)
