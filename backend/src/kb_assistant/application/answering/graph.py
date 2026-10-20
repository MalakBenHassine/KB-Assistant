"""The question-answering workflow as an explicit LangGraph state machine (ADR-0007).

    START → classify_intent ─┬─ out_of_scope / chitchat → polite_refusal → END
                             └─ kb_question → rewrite_query → retrieve → grade_retrieval
           grade_retrieval ─┬─ good → rerank → generate → validate_citations ─┬─ END
                            │                     └─ (insufficient / outage) ─┴─ abstain → END
                            ├─ weak or empty, retries left → rewrite_query
                            └─ weak or empty, retries exhausted → clarify_or_abstain → END

Rules the code enforces:
- Routing functions are pure functions of the state, tested exhaustively.
- Nodes return partial updates and never mutate their input.
- The retrieval scope comes from the use case (built from the Principal); no
  node reads departments or tenants from anywhere else.
- The loop is bounded by `max_retries`, LLM calls by `max_llm_calls`, and the
  whole graph by LangGraph's recursion limit.
- Nodes emit progress through the stream writer: `status` events, validated
  `sentence` events, and the final `message` for refusals and abstentions.
"""

import logging
import operator
import secrets
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Protocol, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from kb_assistant.application.answering.citations import (
    CheckedSentence,
    SentenceBuffer,
    check_sentence,
)
from kb_assistant.application.answering.prompts import (
    ANSWER_SYSTEM,
    CLASSIFY_SYSTEM,
    INSUFFICIENT,
    INTENT_SCHEMA,
    RELEVANCE_SCHEMA,
    RELEVANCE_SYSTEM,
    REWRITE_RETRY_SYSTEM,
    REWRITE_SCHEMA,
    REWRITE_SYSTEM,
    answer_user,
    question_block,
    relevance_user,
    rewrite_user,
)
from kb_assistant.domain.chat import (
    AbstainReason,
    Citation,
    HydratedChunk,
    Intent,
    RetrievalGrade,
    RetrievedChunk,
    TokenUsage,
)
from kb_assistant.domain.documents import RetrievalScope
from kb_assistant.domain.errors import ProviderUnavailableError
from kb_assistant.domain.ports import ChatModel, Embedder, Reranker, VectorStore

log = logging.getLogger(__name__)

Hydrator = Callable[[list[str], RetrievalScope], Awaitable[list[HydratedChunk]]]


def _merge(left: dict[str, float], right: dict[str, float]) -> dict[str, float]:
    return {**left, **right}


class GraphState(TypedDict, total=False):
    # identity: set once by the use case from the authenticated Principal
    request_id: str
    scope: RetrievalScope
    # conversation
    question: str
    conversation_summary: str | None
    # routing
    intent: Intent
    rewritten_query: str
    previous_queries: list[str]
    retry_count: int
    llm_calls: int
    # retrieval
    retrieved_chunks: list[RetrievedChunk]
    hydration_rejections: int
    retrieval_grade: RetrievalGrade
    reranked_chunks: list[RetrievedChunk]
    # outcome
    answer: str | None
    sentences: list[CheckedSentence]
    dropped_sentences: int
    citations: list[Citation]
    needs_clarification: bool
    abstained: bool
    abstain_reason: AbstainReason | None
    # telemetry
    node_timings_ms: Annotated[dict[str, float], _merge]
    token_usage: Annotated[TokenUsage, operator.add]


@dataclass(frozen=True, slots=True)
class AnswerPolicy:
    """Values live in configuration; Phase 4 calibrates them on the eval set."""

    top_k: int = 20
    rerank_top_n: int = 6
    min_score: float = 0.2
    max_retries: int = 1
    llm_relevance_check: bool = False
    max_llm_calls: int = 6
    structured_max_tokens: int = 400
    answer_max_tokens: int = 1200

    def thresholds(self) -> dict[str, Any]:
        """Stored with every answer, so a threshold change is visible in the data."""
        return {
            "top_k": self.top_k,
            "rerank_top_n": self.rerank_top_n,
            "min_score": self.min_score,
            "max_retries": self.max_retries,
            "llm_relevance_check": self.llm_relevance_check,
        }


@dataclass(frozen=True, slots=True)
class GraphDeps:
    embedder: Embedder
    vectors: VectorStore
    hydrate: Hydrator
    chat: ChatModel
    reranker: Reranker | None
    policy: AnswerPolicy
    nonce: Callable[[], str] = lambda: secrets.token_hex(8)


MESSAGES: Mapping[AbstainReason, str] = {
    AbstainReason.NO_ACCESSIBLE_DOCUMENTS: (
        "You don't have access to any department's documents yet, so I can't answer. "
        "Ask an administrator to add you to a department."
    ),
    AbstainReason.NO_RELEVANT_DOCUMENTS: (
        "I couldn't find anything about this in the documents you can access, so I don't know."
    ),
    AbstainReason.WEAK_EVIDENCE: (
        "I found documents that might be related, but nothing that clearly answers your "
        "question. Could you rephrase it, or say which policy, team or product you mean?"
    ),
    AbstainReason.INSUFFICIENT_EVIDENCE: (
        "The documents I found don't contain the answer to this question, so I don't know."
    ),
    AbstainReason.UNSUPPORTED_ANSWER: (
        "I couldn't build an answer that is fully supported by your documents, so I don't know."
    ),
    AbstainReason.ASSISTANT_UNAVAILABLE: (
        "The assistant is temporarily unavailable. Please try again in a moment."
    ),
    AbstainReason.NOT_A_KNOWLEDGE_QUESTION: (
        "I can only answer questions about your organization's documents, and this one is "
        "outside them."
    ),
}
CHITCHAT_REPLY = (
    "Hello! I answer questions about your organization's documents: policies, runbooks, "
    "product specs and more. What would you like to know?"
)


class LlmBudgetExceededError(Exception):
    """More language-model calls than `max_llm_calls` in one request."""


# ----------------------------------------------------------------------------- routing


def route_after_classify(state: GraphState) -> Literal["rewrite_query", "polite_refusal"]:
    return "rewrite_query" if state.get("intent") is Intent.KB_QUESTION else "polite_refusal"


def route_after_retrieve(state: GraphState) -> Literal["grade_retrieval", "abstain"]:
    return "abstain" if state.get("abstain_reason") else "grade_retrieval"


def route_after_grade(
    state: GraphState, policy: AnswerPolicy
) -> Literal["rerank", "rewrite_query", "clarify_or_abstain"]:
    if state.get("retrieval_grade") is RetrievalGrade.GOOD:
        return "rerank"
    if not state["scope"].department_ids:
        return "clarify_or_abstain"  # rewording cannot create access
    if state.get("retry_count", 0) < policy.max_retries:
        return "rewrite_query"
    return "clarify_or_abstain"


def route_after_generate(state: GraphState) -> Literal["validate_citations", "abstain"]:
    return "abstain" if state.get("abstain_reason") else "validate_citations"


def route_after_validate(state: GraphState) -> Literal["abstain", "__end__"]:
    return "__end__" if state.get("citations") else "abstain"  # "__end__" is END


# ----------------------------------------------------------------------------- nodes


class _Node(Protocol):
    """A node. LangGraph matches nodes on the parameter *name* `state`, which
    `Callable[[GraphState], ...]` would erase."""

    def __call__(self, state: GraphState) -> Awaitable[dict[str, Any]]: ...


def _timed(name: str, fn: _Node) -> _Node:
    async def node(state: GraphState) -> dict[str, Any]:
        started = time.perf_counter()
        update = await fn(state)
        update["node_timings_ms"] = {name: round((time.perf_counter() - started) * 1000, 2)}
        return update

    return node


def _emit(event: dict[str, Any]) -> None:
    get_stream_writer()(event)


def _spend_llm_call(state: GraphState, policy: AnswerPolicy) -> int:
    used = state.get("llm_calls", 0) + 1
    if used > policy.max_llm_calls:
        raise LlmBudgetExceededError
    return used


def build_answer_graph(deps: GraphDeps) -> Any:
    policy = deps.policy

    async def classify_intent(state: GraphState) -> dict[str, Any]:
        calls = _spend_llm_call(state, policy)
        try:
            reply = await deps.chat.structured(
                system=CLASSIFY_SYSTEM,
                user=question_block(state["question"], state.get("conversation_summary")),
                schema=INTENT_SCHEMA,
                max_output_tokens=policy.structured_max_tokens,
            )
        except ProviderUnavailableError:
            # Fail open to retrieval: it abstains on its own if nothing is relevant.
            return {"intent": Intent.KB_QUESTION, "llm_calls": calls}
        raw = reply.value.get("intent")
        intent = Intent(raw) if raw in set(Intent) else Intent.KB_QUESTION
        return {"intent": intent, "llm_calls": calls, "token_usage": reply.usage}

    async def rewrite_query(state: GraphState) -> dict[str, Any]:
        is_retry = "rewritten_query" in state
        previous = [*state.get("previous_queries", [])]
        if is_retry:
            previous.append(state["rewritten_query"])
        update: dict[str, Any] = {
            "previous_queries": previous,
            "retry_count": state.get("retry_count", 0) + (1 if is_retry else 0),
        }
        try:
            update["llm_calls"] = _spend_llm_call(state, policy)
            reply = await deps.chat.structured(
                system=REWRITE_RETRY_SYSTEM if is_retry else REWRITE_SYSTEM,
                user=rewrite_user(state["question"], state.get("conversation_summary"), previous),
                schema=REWRITE_SCHEMA,
                max_output_tokens=policy.structured_max_tokens,
            )
            query = str(reply.value.get("query", "")).strip()
            update["token_usage"] = reply.usage
        except (ProviderUnavailableError, LlmBudgetExceededError):
            query = ""
        update["rewritten_query"] = query[:1000] or state["question"]
        return update

    async def retrieve(state: GraphState) -> dict[str, Any]:
        _emit({"type": "status", "stage": "retrieving"})
        scope = state["scope"]
        if not scope.department_ids:
            return {"retrieved_chunks": [], "hydration_rejections": 0}
        try:
            vector = await deps.embedder.embed_query(state["rewritten_query"])
            matches = await deps.vectors.query(vector, scope, policy.top_k)
        except ProviderUnavailableError:
            return {"abstain_reason": AbstainReason.ASSISTANT_UNAVAILABLE}
        hydrated = {c.vector_id: c for c in await deps.hydrate([m.id for m in matches], scope)}
        retrieved = [
            RetrievedChunk(chunk=hydrated[m.id], score=m.score) for m in matches if m.id in hydrated
        ]
        rejected = len(matches) - len(retrieved)
        if rejected:
            # Archived versions are expected here; a foreign tenant or department is not.
            log.warning(
                "retrieval.hydration_rejections",
                extra={"request_id": state.get("request_id"), "count": rejected},
            )
        return {"retrieved_chunks": retrieved, "hydration_rejections": rejected}

    async def grade_retrieval(state: GraphState) -> dict[str, Any]:
        chunks = state.get("retrieved_chunks", [])
        if not chunks:
            return {"retrieval_grade": RetrievalGrade.EMPTY}
        if max(c.score for c in chunks) < policy.min_score:
            return {"retrieval_grade": RetrievalGrade.WEAK}
        if not policy.llm_relevance_check:
            return {"retrieval_grade": RetrievalGrade.GOOD}
        try:
            calls = _spend_llm_call(state, policy)
            top = chunks[: policy.rerank_top_n]
            reply = await deps.chat.structured(
                system=RELEVANCE_SYSTEM,
                user=relevance_user(
                    state["question"],
                    [(f"S{i + 1}", c.chunk) for i, c in enumerate(top)],
                    deps.nonce(),
                ),
                schema=RELEVANCE_SCHEMA,
                max_output_tokens=policy.structured_max_tokens,
            )
        except (ProviderUnavailableError, LlmBudgetExceededError):
            return {"retrieval_grade": RetrievalGrade.GOOD}  # the score threshold already passed
        grade = RetrievalGrade.GOOD if reply.value.get("relevant") is True else RetrievalGrade.WEAK
        return {"retrieval_grade": grade, "llm_calls": calls, "token_usage": reply.usage}

    async def rerank(state: GraphState) -> dict[str, Any]:
        chunks = state["retrieved_chunks"]
        ordered = sorted(chunks, key=lambda c: c.score, reverse=True)[: policy.rerank_top_n]
        if deps.reranker is not None:
            try:
                ordered = await deps.reranker.rerank(
                    state["rewritten_query"], chunks, policy.rerank_top_n
                )
            except ProviderUnavailableError:
                log.warning("rerank.unavailable_fallback_to_vector_order")
        return {"reranked_chunks": ordered}

    async def generate(state: GraphState) -> dict[str, Any]:
        _emit({"type": "status", "stage": "generating"})
        try:
            calls = _spend_llm_call(state, policy)
        except LlmBudgetExceededError:
            return {"abstain_reason": AbstainReason.ASSISTANT_UNAVAILABLE}
        sources = [(f"S{i + 1}", c.chunk) for i, c in enumerate(state["reranked_chunks"])]
        allowed = {alias for alias, _ in sources}
        buffer = SentenceBuffer()
        kept: list[CheckedSentence] = []
        dropped = 0
        insufficient = False
        usage = TokenUsage()

        def handle(sentence: str) -> None:
            nonlocal dropped, insufficient
            if INSUFFICIENT in sentence:
                insufficient = True
                return
            checked = check_sentence(sentence, allowed)
            if checked is None:
                dropped += 1
                return
            kept.append(checked)
            _emit({"type": "sentence", "text": checked.text, "citations": list(checked.aliases)})

        try:
            async for chunk in deps.chat.stream(
                system=ANSWER_SYSTEM,
                user=answer_user(
                    state["question"], sources, deps.nonce(), state.get("conversation_summary")
                ),
                max_output_tokens=policy.answer_max_tokens,
            ):
                if chunk.usage is not None:
                    usage = chunk.usage
                    continue
                for sentence in buffer.feed(chunk.text):
                    handle(sentence)
            for sentence in buffer.flush():
                handle(sentence)
        except ProviderUnavailableError:
            if not kept:
                return {"abstain_reason": AbstainReason.ASSISTANT_UNAVAILABLE, "llm_calls": calls}
            log.warning("generate.stream_interrupted", extra={"kept": len(kept)})

        update: dict[str, Any] = {
            "sentences": kept,
            "dropped_sentences": dropped,
            "answer": " ".join(s.text for s in kept) or None,
            "llm_calls": calls,
            "token_usage": usage,
        }
        if insufficient and not kept:
            update["abstain_reason"] = AbstainReason.INSUFFICIENT_EVIDENCE
        return update

    async def validate_citations(state: GraphState) -> dict[str, Any]:
        by_alias = {f"S{i + 1}": c.chunk for i, c in enumerate(state["reranked_chunks"])}
        order: list[str] = []
        for sentence in state.get("sentences", []):
            for alias in sentence.aliases:
                if alias in by_alias and alias not in order:
                    order.append(alias)
        citations = [
            Citation(
                alias=alias,
                chunk_id=by_alias[alias].chunk_id,
                document_id=by_alias[alias].document_id,
                document_title=by_alias[alias].document_title,
                heading_path=by_alias[alias].heading_path,
                page_start=by_alias[alias].page_start,
                page_end=by_alias[alias].page_end,
            )
            for alias in order
        ]
        if state.get("dropped_sentences"):
            log.info("generate.sentences_dropped", extra={"count": state["dropped_sentences"]})
        update: dict[str, Any] = {"citations": citations, "abstained": not citations}
        if not citations:
            update["abstain_reason"] = AbstainReason.UNSUPPORTED_ANSWER
        return update

    async def clarify_or_abstain(state: GraphState) -> dict[str, Any]:
        if not state["scope"].department_ids:
            reason = AbstainReason.NO_ACCESSIBLE_DOCUMENTS
        elif state.get("retrieval_grade") is RetrievalGrade.WEAK:
            reason = AbstainReason.WEAK_EVIDENCE
        else:
            reason = AbstainReason.NO_RELEVANT_DOCUMENTS
        text = MESSAGES[reason]
        _emit({"type": "message", "text": text})
        return {
            "answer": text,
            "abstained": True,
            "abstain_reason": reason,
            "needs_clarification": reason is AbstainReason.WEAK_EVIDENCE,
            "citations": [],
        }

    async def abstain(state: GraphState) -> dict[str, Any]:
        reason = state.get("abstain_reason") or AbstainReason.UNSUPPORTED_ANSWER
        text = MESSAGES[reason]
        _emit({"type": "message", "text": text})
        return {"answer": text, "abstained": True, "abstain_reason": reason, "citations": []}

    async def polite_refusal(state: GraphState) -> dict[str, Any]:
        if state.get("intent") is Intent.CHITCHAT:
            text, abstained = CHITCHAT_REPLY, False
        else:
            text, abstained = MESSAGES[AbstainReason.NOT_A_KNOWLEDGE_QUESTION], True
        _emit({"type": "message", "text": text})
        return {
            "answer": text,
            "abstained": abstained,
            "abstain_reason": AbstainReason.NOT_A_KNOWLEDGE_QUESTION if abstained else None,
            "citations": [],
        }

    graph = StateGraph(GraphState)
    for name, fn in (
        ("classify_intent", classify_intent),
        ("rewrite_query", rewrite_query),
        ("retrieve", retrieve),
        ("grade_retrieval", grade_retrieval),
        ("rerank", rerank),
        ("generate", generate),
        ("validate_citations", validate_citations),
        ("clarify_or_abstain", clarify_or_abstain),
        ("abstain", abstain),
        ("polite_refusal", polite_refusal),
    ):
        graph.add_node(name, _timed(name, fn))

    graph.add_edge(START, "classify_intent")
    graph.add_conditional_edges(
        "classify_intent", route_after_classify, ["rewrite_query", "polite_refusal"]
    )
    graph.add_edge("rewrite_query", "retrieve")
    graph.add_conditional_edges("retrieve", route_after_retrieve, ["grade_retrieval", "abstain"])
    graph.add_conditional_edges(
        "grade_retrieval",
        lambda s: route_after_grade(s, policy),
        ["rerank", "rewrite_query", "clarify_or_abstain"],
    )
    graph.add_edge("rerank", "generate")
    graph.add_conditional_edges("generate", route_after_generate, ["validate_citations", "abstain"])
    graph.add_conditional_edges("validate_citations", route_after_validate, ["abstain", END])
    for terminal in ("clarify_or_abstain", "abstain", "polite_refusal"):
        graph.add_edge(terminal, END)
    return graph.compile(name="answer")


def recursion_limit(policy: AnswerPolicy) -> int:
    """A backstop above the longest legitimate path (each retry adds 3 steps)."""
    return 10 + 3 * policy.max_retries
