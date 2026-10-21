"""Running a question through the real workflow and recording what it did.

The graph is the one the API runs, built by the composition root with a given
policy; the scope comes from `retrieval_scope()`, as for a request. Nothing is
mocked: the measured system is the shipped one.
"""

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from kb_assistant.application.answering.graph import (
    AnswerPolicy,
    GraphState,
    LlmBudgetExceededError,
    recursion_limit,
)
from kb_assistant.application.answering.service import (
    UnitOfWorkFactory,
    initial_state,
    retrieval_scope,
)
from kb_assistant.domain.chat import Intent, RetrievedChunk
from kb_assistant.domain.ids import DocumentId
from kb_assistant.domain.principal import Principal
from kb_assistant.evaluation.calibration import Probe
from kb_assistant.evaluation.dataset import Behaviour, EvalQuestion
from kb_assistant.evaluation.metrics import QuestionResult, dedupe, facts_in, sentence_support

UNKNOWN_DOCUMENT = "<not in the corpus>"


@dataclass(frozen=True, slots=True)
class Asker:
    principal: Principal
    readable: frozenset[str]  # corpus paths this person may read


def behaviour_of(final: Mapping[str, Any]) -> Behaviour:
    if final.get("citations"):
        return Behaviour.ANSWER
    if final.get("intent") is Intent.CHITCHAT and not final.get("abstained"):
        return Behaviour.REPLY
    return Behaviour.ABSTAIN


class Evaluator:
    def __init__(
        self,
        *,
        graph: Any,
        policy: AnswerPolicy,
        uow_factory: UnitOfWorkFactory,
        documents: Mapping[DocumentId, str],
    ) -> None:
        self._graph = graph
        self._recursion_limit = recursion_limit(policy)
        self._uow_factory = uow_factory
        self._documents = documents

    async def _final_state(self, question: EvalQuestion, asker: Asker) -> dict[str, Any]:
        async with self._uow_factory() as uow:
            await uow.bind(tenant_id=asker.principal.tenant_id, user_id=asker.principal.user_id)
            scope = await retrieval_scope(uow, asker.principal, None)
        state: GraphState = initial_state(
            request_id=f"eval-{question.id}",
            scope=scope,
            question=question.question,
            conversation_summary=None,
        )
        final: dict[str, Any] = dict(state)
        try:
            async for values in self._graph.astream(
                state, stream_mode="values", config={"recursion_limit": self._recursion_limit}
            ):
                final = values
        except LlmBudgetExceededError:
            final["abstained"] = True
        return final

    async def run(self, question: EvalQuestion, asker: Asker) -> QuestionResult:
        started = time.perf_counter()
        final = await self._final_state(question, asker)
        latency_ms = (time.perf_counter() - started) * 1000
        retrieved: list[RetrievedChunk] = sorted(
            final.get("retrieved_chunks") or [], key=lambda c: c.score, reverse=True
        )
        paths = [self._documents.get(c.chunk.document_id, UNKNOWN_DOCUMENT) for c in retrieved]
        behaviour = behaviour_of(final)
        answered = behaviour is Behaviour.ANSWER
        # Only an answer states facts; an abstention message never counts as one.
        answer = str(final.get("answer") or "")
        found = facts_in(answer, question.must_include) if answered else ()
        by_alias = {f"S{i + 1}": c.chunk for i, c in enumerate(final.get("reranked_chunks") or [])}
        support = tuple(
            sentence_support(s.text, [by_alias[a].text for a in s.aliases if a in by_alias])
            for s in final.get("sentences") or []
        )
        reason = final.get("abstain_reason")
        return QuestionResult(
            id=question.id,
            category=question.category,
            expected=question.expected,
            behaviour=behaviour,
            answer=answer,
            abstain_reason=reason.value if reason else None,
            retrieved_documents=dedupe(paths),
            top_score=retrieved[0].score if retrieved else None,
            cited_documents=tuple(
                self._documents.get(c.document_id, UNKNOWN_DOCUMENT)
                for c in final.get("citations") or []
            ),
            sentence_support=support if answered else (),
            missing_facts=tuple(f for f in question.must_include if f not in found),
            forbidden_facts=facts_in(answer, question.must_not_include) if answered else (),
            leaked_chunks=sum(1 for p in paths if p not in asker.readable),
            latency_ms=latency_ms,
            expected_documents=question.expected_documents,
        )


def probe_of(result: QuestionResult) -> Probe:
    return Probe(
        id=result.id,
        expected=result.expected,
        top_score=result.top_score,
        answered_without_threshold=result.behaviour is Behaviour.ANSWER,
    )
