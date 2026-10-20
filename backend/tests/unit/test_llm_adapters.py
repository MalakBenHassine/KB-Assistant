"""Language-model adapters: OpenAI (with a fake client), the circuit breaker, the
offline extractive model, and the Pinecone reranker."""

import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx2 as httpx
import pytest
from openai import APITimeoutError

from kb_assistant.application.answering.prompts import (
    INTENT_SCHEMA,
    REWRITE_SCHEMA,
    answer_user,
    question_block,
)
from kb_assistant.domain.chat import RetrievedChunk, StreamChunk, TokenUsage
from kb_assistant.domain.errors import ProviderUnavailableError
from kb_assistant.domain.ids import DepartmentId
from kb_assistant.infrastructure.llm.extractive import ExtractiveChatModel
from kb_assistant.infrastructure.llm.openai_chat import OpenAIChatModel
from kb_assistant.infrastructure.rerank import PineconeReranker
from kb_assistant.infrastructure.resilience import CircuitBreaker, CircuitOpenError
from tests.chat_fakes import chunk

HR = DepartmentId(uuid4())
REQUEST = httpx.Request("POST", "https://api.openai.example/v1/responses")


# ----------------------------------------------------------------------------- circuit


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_breaker_opens_after_consecutive_failures_and_probes_after_the_pause() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=2, reset_after_s=30, clock=clock)
    breaker.record_failure()
    breaker.before_call()  # still closed
    breaker.record_failure()
    with pytest.raises(CircuitOpenError):
        breaker.before_call()

    clock.now = 31
    assert breaker.state == "half_open"
    breaker.before_call()  # the probe is allowed
    breaker.record_failure()  # and fails: open again immediately
    with pytest.raises(CircuitOpenError):
        breaker.before_call()

    clock.now = 62
    breaker.before_call()
    breaker.record_success()
    assert breaker.state == "closed"


def test_a_success_resets_the_failure_count() -> None:
    breaker = CircuitBreaker(failure_threshold=2, reset_after_s=30, clock=FakeClock())
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    breaker.before_call()


# ----------------------------------------------------------------------------- OpenAI


class _Events:
    def __init__(self, events: list[Any]) -> None:
        self._events = events

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[Any]:
        for event in self._events:
            yield event


class FakeResponses:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def openai_model(responses: FakeResponses, **kwargs: Any) -> OpenAIChatModel:
    client: Any = SimpleNamespace(responses=responses)
    return OpenAIChatModel(
        client, model="gpt-test", reasoning_effort="low", timeout_s=5, attempts=2, **kwargs
    )


async def test_structured_output_requests_a_strict_json_schema() -> None:
    usage = SimpleNamespace(input_tokens=10, output_tokens=2)
    responses = FakeResponses(
        [SimpleNamespace(output_text=json.dumps({"intent": "chitchat"}), usage=usage)]
    )
    reply = await openai_model(responses).structured(
        system="s", user="u", schema=INTENT_SCHEMA, max_output_tokens=50
    )
    assert reply.value == {"intent": "chitchat"}
    assert reply.usage == TokenUsage(10, 2)
    (call,) = responses.calls
    assert call["text"]["format"]["strict"] is True
    assert call["text"]["format"]["schema"] == INTENT_SCHEMA.schema
    assert call["store"] is False  # nothing kept by the provider


async def test_malformed_json_becomes_an_empty_reply_not_a_crash() -> None:
    responses = FakeResponses([SimpleNamespace(output_text="not json", usage=None)])
    reply = await openai_model(responses).structured(
        system="s", user="u", schema=REWRITE_SCHEMA, max_output_tokens=50
    )
    assert reply.value == {}


async def test_streaming_yields_deltas_then_usage() -> None:
    usage = SimpleNamespace(input_tokens=50, output_tokens=7)
    events = _Events(
        [
            SimpleNamespace(type="response.output_text.delta", delta="Leave is "),
            SimpleNamespace(type="response.output_text.delta", delta="25 days [S1]."),
            SimpleNamespace(type="response.completed", response=SimpleNamespace(usage=usage)),
        ]
    )
    chunks = [
        c
        async for c in openai_model(FakeResponses([events])).stream(
            system="s", user="u", max_output_tokens=50
        )
    ]
    assert "".join(c.text for c in chunks) == "Leave is 25 days [S1]."
    assert chunks[-1] == StreamChunk(usage=TokenUsage(50, 7))


async def test_outages_open_the_breaker_and_then_fail_fast() -> None:
    responses = FakeResponses([APITimeoutError(REQUEST)] * 4)
    breaker = CircuitBreaker(failure_threshold=2, reset_after_s=60, clock=FakeClock())
    model = openai_model(responses, breaker=breaker)
    for _ in range(2):
        with pytest.raises(ProviderUnavailableError):
            await model.structured(system="s", user="u", schema=INTENT_SCHEMA, max_output_tokens=5)
    calls_before = len(responses.calls)
    with pytest.raises(ProviderUnavailableError):
        await model.structured(system="s", user="u", schema=INTENT_SCHEMA, max_output_tokens=5)
    assert len(responses.calls) == calls_before  # open: the provider was not called


# ----------------------------------------------------------------------------- extractive


async def test_extractive_model_classifies_greetings_and_questions() -> None:
    model = ExtractiveChatModel()
    hello = await model.structured(
        system="", user=question_block("Hello!"), schema=INTENT_SCHEMA, max_output_tokens=10
    )
    question = await model.structured(
        system="",
        user=question_block("How many days of leave?"),
        schema=INTENT_SCHEMA,
        max_output_tokens=10,
    )
    assert hello.value == {"intent": "chitchat"}
    assert question.value == {"intent": "kb_question"}


async def test_extractive_model_quotes_the_best_sentence_and_cites_it() -> None:
    sources = [
        ("S1", chunk("Welcome to Acme. Annual leave is 25 days per year. Ask HR.", HR)),
        ("S2", chunk("The cafeteria opens at noon.", HR)),
    ]
    prompt = answer_user("How many days of annual leave?", sources, "nonce", None)
    text = "".join(
        [
            c.text
            async for c in ExtractiveChatModel().stream(system="", user=prompt, max_output_tokens=9)
        ]
    )
    assert text == "Annual leave is 25 days per year [S1]."


async def test_extractive_model_ignores_a_single_coincidental_word() -> None:
    sources = [
        ("S1", chunk("Parental leave is open to employees after six months.", HR)),
        ("S2", chunk("Leave type | Days. Annual | 25.", HR)),
    ]
    prompt = answer_user("Who is eligible for parental leave?", sources, "n", None)
    text = "".join(
        [
            c.text
            async for c in ExtractiveChatModel().stream(system="", user=prompt, max_output_tokens=9)
        ]
    )
    assert text == "Parental leave is open to employees after six months [S1]."


async def test_extractive_model_admits_when_nothing_matches() -> None:
    prompt = answer_user(
        "What is the parking policy?", [("S1", chunk("Lunch is at noon.", HR))], "n", None
    )
    text = "".join(
        [
            c.text
            async for c in ExtractiveChatModel().stream(system="", user=prompt, max_output_tokens=9)
        ]
    )
    assert text == "INSUFFICIENT_EVIDENCE"


# ----------------------------------------------------------------------------- reranker


async def test_pinecone_reranker_sends_ids_and_text_and_keeps_the_new_order() -> None:
    calls: list[dict[str, Any]] = []

    async def rerank(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return SimpleNamespace(
            data=[SimpleNamespace(index=1, score=0.9), SimpleNamespace(index=0, score=0.2)]
        )

    client: Any = SimpleNamespace(inference=SimpleNamespace(rerank=rerank))
    first, second = chunk("A", HR), chunk("B", HR)
    ranked = await PineconeReranker(client, model="bge-reranker-v2-m3").rerank(
        "q", [RetrievedChunk(first, 0.5), RetrievedChunk(second, 0.4)], top_n=5
    )
    assert [r.chunk.text for r in ranked] == ["B", "A"]
    assert ranked[0].rerank_score == 0.9
    (call,) = calls
    assert call["documents"] == [
        {"id": first.vector_id, "text": "A"},
        {"id": second.vector_id, "text": "B"},
    ]
    assert call["top_n"] == 2
    assert call["return_documents"] is False
