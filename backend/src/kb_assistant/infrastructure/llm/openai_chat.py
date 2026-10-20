"""OpenAI Responses API adapter (openai SDK 3.x, checked 2026-10-20).

- Structured calls use JSON-schema output (`strict`), parsed and returned as a
  plain dict; the application validates the fields it reads.
- Streaming yields `response.output_text.delta` events, then the usage from
  `response.completed`.
- Only calls that have produced nothing yet are retried: a stream interrupted
  after its first token is reported, not silently restarted.
- A circuit breaker stops hammering a failing provider.
"""

import json
import time
from collections.abc import AsyncIterator
from typing import Any, Literal

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)

from kb_assistant.domain.chat import JsonSchema, StreamChunk, StructuredReply, TokenUsage
from kb_assistant.domain.errors import ProviderUnavailableError
from kb_assistant.infrastructure.resilience import CircuitBreaker, CircuitOpenError, retrying

_TRANSIENT = (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError)
Effort = Literal["none", "low", "medium", "high"]


def _usage(raw: Any) -> TokenUsage:
    if raw is None:
        return TokenUsage()
    return TokenUsage(input_tokens=raw.input_tokens, output_tokens=raw.output_tokens)


class OpenAIChatModel:
    def __init__(
        self,
        client: AsyncOpenAI,
        *,
        model: str,
        reasoning_effort: Effort,
        timeout_s: float,
        attempts: int = 3,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._effort = reasoning_effort
        self._timeout_s = timeout_s
        self._attempts = attempts
        self._breaker = breaker or CircuitBreaker(
            failure_threshold=5, reset_after_s=30, clock=time.monotonic
        )

    @property
    def model_id(self) -> str:
        return self._model

    def _unavailable(self, exc: BaseException) -> ProviderUnavailableError:
        return ProviderUnavailableError("language_model", type(exc).__name__)

    async def structured(
        self, *, system: str, user: str, schema: JsonSchema, max_output_tokens: int
    ) -> StructuredReply:
        try:
            self._breaker.before_call()
            async for attempt in retrying(attempts=self._attempts, retry_on=_TRANSIENT):
                with attempt:
                    response = await self._client.responses.create(
                        model=self._model,
                        instructions=system,
                        input=user,
                        max_output_tokens=max_output_tokens,
                        reasoning={"effort": self._effort},
                        text={
                            "format": {
                                "type": "json_schema",
                                "name": schema.name,
                                "schema": schema.schema,
                                "strict": True,
                            }
                        },
                        store=False,
                        timeout=self._timeout_s,
                    )
        except CircuitOpenError as exc:
            raise self._unavailable(exc) from exc
        except (*_TRANSIENT, APIStatusError) as exc:
            self._breaker.record_failure()
            raise self._unavailable(exc) from exc
        self._breaker.record_success()
        try:
            value = json.loads(response.output_text)
        except json.JSONDecodeError:
            value = {}
        return StructuredReply(
            value=value if isinstance(value, dict) else {}, usage=_usage(response.usage)
        )

    async def stream(
        self, *, system: str, user: str, max_output_tokens: int
    ) -> AsyncIterator[StreamChunk]:
        try:
            self._breaker.before_call()
            async for attempt in retrying(attempts=self._attempts, retry_on=_TRANSIENT):
                with attempt:
                    events = await self._client.responses.create(
                        model=self._model,
                        instructions=system,
                        input=user,
                        max_output_tokens=max_output_tokens,
                        reasoning={"effort": self._effort},
                        store=False,
                        stream=True,
                        timeout=self._timeout_s,
                    )
        except CircuitOpenError as exc:
            raise self._unavailable(exc) from exc
        except (*_TRANSIENT, APIStatusError) as exc:
            self._breaker.record_failure()
            raise self._unavailable(exc) from exc

        try:
            async for event in events:
                if event.type == "response.output_text.delta":
                    yield StreamChunk(text=event.delta)
                elif event.type == "response.completed":
                    yield StreamChunk(usage=_usage(event.response.usage))
                elif event.type in ("response.failed", "error"):
                    raise ProviderUnavailableError("language_model", "the response failed")
        except (*_TRANSIENT, APIStatusError) as exc:
            self._breaker.record_failure()
            raise self._unavailable(exc) from exc
        self._breaker.record_success()
