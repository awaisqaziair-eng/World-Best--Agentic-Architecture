# ADR-007 — Explicit `finish` + verification loop

**Status:** Accepted (amended in v1.1) · **Date:** 2026-09-24

## Context
"No tool call means done" is ambiguous: models also stop to narrate intent ("Let me now run the tests."). And a claimed success is not a verified one.

## Decision
- Completion is an explicit `finish(answer, artifacts)` call, which triggers verification: a task's `verify_command` (exit 0) or, if acceptance criteria exist, a separate judge call; failures return to the agent as feedback, at most `max_verify_rounds` (2) times, after which the answer is accepted as `finished_unverified`.
- **v1.1 amendment:** a prose-only reply that *reads as final* (its last sentence does not announce an intended action and it does not end with a colon) is accepted immediately; intent-like prose gets one nudge. Measured in v1: all 4 prose replies were complete answers and each nudge cost a full-context turn.

## Consequences
+ Clear termination semantics; verification can only improve outcomes, never deadlock.
− The judge is itself a model and can be wrong; command verification is preferred whenever a check can be scripted.
