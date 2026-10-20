# 0007. LangGraph for the question workflow

- **Status:** Proposed
- **Date:** 2026-10-20

## Context

Answering is not a single retrieve-then-generate call: it classifies intent,
rewrites the query, grades retrieval, loops a bounded number of times, then
either reranks and generates or asks for clarification or abstains, and finally
validates citations. That is a state machine with conditional transitions and a
loop. It must be testable node by node, observable per step, and impossible to
send into an unbounded loop.

## Options considered

### A. Hand-written async function with `if` / `while`
No dependency, fully transparent. But routing, state and telemetry end up
tangled; the loop bound is a convention; there is no standard way to visualise
or trace the flow.

### B. A linear LCEL chain with branches
Good for linear pipelines; loops and multi-way routing are awkward.

### C. LangGraph `StateGraph` with a typed state and conditional edges
Nodes are plain functions from state to a partial state update; routing
functions are pure and testable; the graph can be rendered as a diagram;
streaming is built in; a recursion limit is enforced by the runtime.

## Decision

Option C.

- The state is a `TypedDict` (ARCHITECTURE §5.2). Nodes return partial updates;
  no node mutates its input.
- **Routing functions are pure** (`route_after_grade(state) -> Literal[...]`) and
  are unit-tested exhaustively, separately from the nodes.
- Nodes receive their dependencies (ports, settings) by closure from a builder
  `build_answer_graph(deps: GraphDeps) -> CompiledGraph`, so tests build the
  graph with fakes. Nothing is global.
- The loop is bounded twice: `retry_count < settings.graph.max_retries` in the
  router, and the LangGraph `recursion_limit` as a backstop. A hard cap on LLM
  calls per request is enforced by a counting wrapper around the `ChatModel`
  port.
- Each node is wrapped in an OpenTelemetry span and records its duration in
  `node_timings_ms`.
- The compiled graph's Mermaid rendering is generated in CI and compared with
  `docs/ARCHITECTURE.md`, so the documented flow and the code cannot drift.
- No checkpointer in v1: a request is short-lived and the conversation summary
  lives in Postgres. Revisit if human-in-the-loop steps appear.
- The exact LangGraph API (version 1.x at the time of writing) is checked
  against the official documentation when Phase 3 starts, and the version is
  pinned in `uv.lock`.

## Consequences

- One more framework dependency on the critical path, isolated in
  `application/graph/`; nodes only talk to our ports
  ([ADR-0006](0006-provider-abstraction.md)).
- New steps (a safety classifier, a query decomposition) are a node and an edge.

## Revisit when

The workflow collapses back to a linear pipeline, or LangGraph's API churn costs
more than the routing code it saves.
