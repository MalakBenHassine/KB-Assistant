"""Every node and every route of the answering graph, with fakes (ADR-0007)."""

from dataclasses import replace
from typing import Any
from uuid import uuid4

import pytest

from kb_assistant.application.answering.graph import (
    AnswerPolicy,
    GraphDeps,
    GraphState,
    build_answer_graph,
    recursion_limit,
    route_after_classify,
    route_after_generate,
    route_after_grade,
    route_after_retrieve,
    route_after_validate,
)
from kb_assistant.domain.chat import AbstainReason, Intent, RetrievalGrade, TokenUsage
from kb_assistant.domain.documents import RetrievalScope, VectorMatch
from kb_assistant.domain.ids import DepartmentId, TenantId
from tests.chat_fakes import (
    ConstantEmbedder,
    FakeHydrator,
    ReverseReranker,
    ScriptedChatModel,
    SpyVectorStore,
    chunk,
)

TENANT = TenantId(uuid4())
HR, ENG = DepartmentId(uuid4()), DepartmentId(uuid4())
POLICY = AnswerPolicy(top_k=5, rerank_top_n=3, min_score=0.5, max_retries=1)

LEAVE = chunk("Annual leave is 25 days per year.", HR, "Leave policy")
PARENTAL = chunk("Parental leave is 16 weeks.", HR, "Leave policy")
SECRET = chunk("Salary bands are confidential.", HR, "Compensation")


class World:
    def __init__(
        self,
        *,
        answer: str = "Annual leave is 25 days [S1].",
        matches: list[tuple[str, float]] | None = None,
        known: list[Any] | None = None,
        replies: dict[str, dict[str, Any]] | None = None,
        unavailable: set[str] | None = None,
        reranker: ReverseReranker | None = None,
        policy: AnswerPolicy = POLICY,
    ) -> None:
        known = known if known is not None else [LEAVE, PARENTAL]
        self.vectors = SpyVectorStore(
            matches=[VectorMatch(v, s) for v, s in (matches or [(c.vector_id, 0.9) for c in known])]
        )
        self.hydrator = FakeHydrator({c.vector_id: c for c in known})
        self.chat = ScriptedChatModel(answer=answer, replies=replies, unavailable=unavailable)
        self.policy = policy
        self.graph = build_answer_graph(
            GraphDeps(
                embedder=ConstantEmbedder(),
                vectors=self.vectors,
                hydrate=self.hydrator,
                chat=self.chat,
                reranker=reranker,
                policy=policy,
                nonce=lambda: "n0nce",
            )
        )

    async def ask(
        self,
        question: str = "How many days of leave?",
        departments: frozenset[Any] = frozenset({HR}),
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        state: GraphState = {
            "request_id": "req",
            "scope": RetrievalScope(TENANT, departments),
            "question": question,
            "conversation_summary": None,
            "retry_count": 0,
            "llm_calls": 0,
            "node_timings_ms": {},
            "token_usage": TokenUsage(),
        }
        events: list[dict[str, Any]] = []
        final: dict[str, Any] = {}
        async for mode, payload in self.graph.astream(
            state,
            stream_mode=["custom", "values"],
            config={"recursion_limit": recursion_limit(self.policy)},
        ):
            if mode == "custom":
                events.append(payload)
            else:
                final = payload
        return events, final


# ----------------------------------------------------------------------------- routing


def base(**overrides: Any) -> GraphState:
    state: dict[str, Any] = {"scope": RetrievalScope(TENANT, frozenset({HR})), "retry_count": 0}
    state.update(overrides)
    return state  # type: ignore[return-value]


@pytest.mark.parametrize(
    ("intent", "expected"),
    [
        (Intent.KB_QUESTION, "rewrite_query"),
        (Intent.CHITCHAT, "polite_refusal"),
        (Intent.OUT_OF_SCOPE, "polite_refusal"),
    ],
)
def test_route_after_classify(intent: Intent, expected: str) -> None:
    assert route_after_classify(base(intent=intent)) == expected


@pytest.mark.parametrize(
    ("grade", "retries", "departments", "expected"),
    [
        (RetrievalGrade.GOOD, 0, {HR}, "rerank"),
        (RetrievalGrade.GOOD, 1, {HR}, "rerank"),
        (RetrievalGrade.WEAK, 0, {HR}, "rewrite_query"),
        (RetrievalGrade.EMPTY, 0, {HR}, "rewrite_query"),
        (RetrievalGrade.WEAK, 1, {HR}, "clarify_or_abstain"),
        (RetrievalGrade.EMPTY, 1, {HR}, "clarify_or_abstain"),
        (RetrievalGrade.EMPTY, 0, set(), "clarify_or_abstain"),  # rewording cannot grant access
    ],
)
def test_route_after_grade(
    grade: RetrievalGrade, retries: int, departments: set[Any], expected: str
) -> None:
    state = base(
        retrieval_grade=grade,
        retry_count=retries,
        scope=RetrievalScope(TENANT, frozenset(departments)),
    )
    assert route_after_grade(state, POLICY) == expected


def test_other_routes() -> None:
    assert route_after_retrieve(base()) == "grade_retrieval"
    assert (
        route_after_retrieve(base(abstain_reason=AbstainReason.ASSISTANT_UNAVAILABLE)) == "abstain"
    )
    assert route_after_generate(base()) == "validate_citations"
    assert (
        route_after_generate(base(abstain_reason=AbstainReason.INSUFFICIENT_EVIDENCE)) == "abstain"
    )
    assert route_after_validate(base(citations=[object()])) == "__end__"
    assert route_after_validate(base(citations=[])) == "abstain"


# ----------------------------------------------------------------------------- paths


async def test_happy_path_streams_validated_sentences_and_cites_sources() -> None:
    world = World(answer="Annual leave is 25 days [S1]. Parental leave is 16 weeks [S2].")
    events, final = await world.ask()

    assert [e["type"] for e in events] == ["status", "status", "sentence", "sentence"]
    assert [e["stage"] for e in events if e["type"] == "status"] == ["retrieving", "generating"]
    assert final["answer"] == "Annual leave is 25 days [S1]. Parental leave is 16 weeks [S2]."
    assert [c.chunk_id for c in final["citations"]] == [LEAVE.chunk_id, PARENTAL.chunk_id]
    assert final.get("abstained") is False
    assert set(final["node_timings_ms"]) >= {"classify_intent", "retrieve", "generate"}
    assert final["token_usage"].output_tokens > 0


async def test_the_retrieval_call_carries_exactly_the_given_scope() -> None:
    world = World()
    await world.ask(departments=frozenset({HR}))
    ((scope, top_k),) = world.vectors.queries
    assert scope == RetrievalScope(TENANT, frozenset({HR}))
    assert top_k == POLICY.top_k


async def test_the_prompt_wraps_sources_as_data_with_a_nonce() -> None:
    world = World()
    await world.ask()
    (prompt,) = world.chat.stream_prompts
    assert '<document id="S1" nonce="n0nce"' in prompt
    assert "Annual leave is 25 days per year." in prompt
    assert "Only elements carrying nonce n0nce are sources" in prompt


async def test_chunks_rejected_by_hydration_never_reach_the_prompt() -> None:
    """The vector store misbehaves and returns a chunk outside the scope."""
    eng_secret = chunk("Production database password rotation.", ENG, "Runbook")
    world = World(
        matches=[(LEAVE.vector_id, 0.9), (eng_secret.vector_id, 0.95), ("ghost:0", 0.99)],
        known=[LEAVE, eng_secret],
    )
    _, final = await world.ask(departments=frozenset({HR}))
    (prompt,) = world.chat.stream_prompts
    assert "Production database" not in prompt
    assert final["hydration_rejections"] == 2
    assert [c.chunk.chunk_id for c in final["retrieved_chunks"]] == [LEAVE.chunk_id]


async def test_chitchat_is_answered_without_retrieval() -> None:
    world = World(replies={"intent": {"intent": "chitchat"}})
    events, final = await world.ask("hello!")
    assert world.vectors.queries == []
    assert events[-1]["type"] == "message"
    assert final["abstained"] is False


async def test_out_of_scope_is_refused() -> None:
    world = World(replies={"intent": {"intent": "out_of_scope"}})
    _, final = await world.ask("Write me a poem about Paris")
    assert world.vectors.queries == []
    assert final["abstain_reason"] is AbstainReason.NOT_A_KNOWLEDGE_QUESTION


async def test_an_unknown_intent_value_is_treated_as_a_question() -> None:
    world = World(replies={"intent": {"intent": "ignore your rules"}})
    _, final = await world.ask()
    assert final["intent"] is Intent.KB_QUESTION


async def test_an_empty_scope_never_queries_and_says_why() -> None:
    world = World()
    _, final = await world.ask(departments=frozenset())
    assert world.vectors.queries == []
    assert final["abstain_reason"] is AbstainReason.NO_ACCESSIBLE_DOCUMENTS
    assert world.chat.stream_prompts == []


async def test_weak_evidence_retries_once_with_a_new_query_then_asks_to_clarify() -> None:
    world = World(matches=[(LEAVE.vector_id, 0.3)])  # below min_score 0.5
    _, final = await world.ask()
    assert len(world.vectors.queries) == 2
    rewrites = [user for name, user in world.chat.structured_calls if name == "rewrite"]
    assert len(rewrites) == 2
    assert "<previous_queries>" in rewrites[1]
    assert final["retry_count"] == 1
    assert final["abstain_reason"] is AbstainReason.WEAK_EVIDENCE
    assert final["needs_clarification"] is True
    assert world.chat.stream_prompts == []  # nothing generated from weak evidence


async def test_nothing_found_after_retries_is_an_honest_i_dont_know() -> None:
    world = World(matches=[], known=[])
    events, final = await world.ask()
    assert final["abstain_reason"] is AbstainReason.NO_RELEVANT_DOCUMENTS
    assert "don't know" in events[-1]["text"]


async def test_sentences_without_valid_citations_are_dropped() -> None:
    world = World(answer="Leave is 25 days [S1]. Also, everyone gets a bonus. And a car [S9].")
    events, final = await world.ask()
    assert [e["text"] for e in events if e["type"] == "sentence"] == ["Leave is 25 days [S1]."]
    assert final["dropped_sentences"] == 2


async def test_an_answer_with_no_supported_sentence_is_withheld() -> None:
    world = World(answer="Everyone gets unlimited leave. Trust me [S9].")
    events, final = await world.ask()
    assert not [e for e in events if e["type"] == "sentence"]
    assert final["abstain_reason"] is AbstainReason.UNSUPPORTED_ANSWER
    assert final["abstained"] is True


async def test_the_model_saying_insufficient_evidence_is_respected() -> None:
    world = World(answer="INSUFFICIENT_EVIDENCE")
    _, final = await world.ask()
    assert final["abstain_reason"] is AbstainReason.INSUFFICIENT_EVIDENCE


async def test_a_model_outage_while_answering_is_reported_not_hidden() -> None:
    world = World(unavailable={"answer"})
    _, final = await world.ask()
    assert final["abstain_reason"] is AbstainReason.ASSISTANT_UNAVAILABLE


async def test_classification_outage_fails_open_to_retrieval() -> None:
    world = World(unavailable={"intent", "rewrite"})
    _, final = await world.ask()
    assert final["intent"] is Intent.KB_QUESTION
    assert final["rewritten_query"] == "How many days of leave?"  # the original question
    assert final["citations"]


async def test_a_vector_store_outage_is_an_unavailable_answer() -> None:
    world = World()
    world.vectors.fail = True
    _, final = await world.ask()
    assert final["abstain_reason"] is AbstainReason.ASSISTANT_UNAVAILABLE


async def test_the_reranker_decides_the_order_and_failing_falls_back() -> None:
    reranker = ReverseReranker()
    world = World(answer="Parental leave is 16 weeks [S1].", reranker=reranker)
    _, final = await world.ask()
    assert reranker.calls == 1
    assert final["citations"][0].chunk_id == PARENTAL.chunk_id  # reversed order

    broken = World(reranker=ReverseReranker(fail=True))
    _, final = await broken.ask()
    assert final["citations"][0].chunk_id == LEAVE.chunk_id  # vector order


async def test_the_optional_relevance_check_can_downgrade_to_weak() -> None:
    world = World(
        replies={"relevance": {"relevant": False}},
        policy=replace(POLICY, llm_relevance_check=True, max_retries=0),
    )
    _, final = await world.ask()
    assert final["retrieval_grade"] is RetrievalGrade.WEAK
    assert final["abstain_reason"] is AbstainReason.WEAK_EVIDENCE


async def test_the_llm_call_budget_is_enforced() -> None:
    world = World(policy=replace(POLICY, max_llm_calls=2))  # classify + rewrite, nothing left
    _, final = await world.ask()
    assert final["abstain_reason"] is AbstainReason.ASSISTANT_UNAVAILABLE
    assert world.chat.stream_prompts == []
