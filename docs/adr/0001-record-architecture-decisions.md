# 0001. Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-10-20

## Context

KB Assistant makes several decisions that are expensive to reverse (vector store,
isolation model, authentication) and several that deliberately deviate from the
initial specification. Six months from now, "why is it like this?" must have an
answer that does not depend on anyone's memory.

## Decision

We keep Architecture Decision Records in `docs/adr/`, numbered, one decision per
file, using [`template.md`](template.md). Every deviation from the specification
gets a record. Accepted records are never edited, only superseded.

## Consequences

- Reviewers can check code against a written decision.
- Writing the "Options considered" section forces us to compare at least two
  options before choosing.
- A small cost per decision, paid once.

## Revisit when

Never; superseding individual records is the mechanism for change.
