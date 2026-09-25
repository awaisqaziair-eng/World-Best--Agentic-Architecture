# ADR-003 — Deterministic harness, stochastic leaves

**Status:** Accepted · **Date:** 2026-09-24

## Context
Model outputs are not reproducible even at temperature 0 on hosted inference. Users still need bounded, predictable, debuggable behaviour, and some procedures (CI repair, report pipelines) should not be left to model judgement.

## Decision
Everything except the model's next action is deterministic code: budgets, termination, context rewrites, verification commands, retries (with injectable RNG), tool ordering. Offer **workflows** (code control flow, LLM/agent leaves, journaled steps that skip on re-run) as the deterministic end of an autonomy dial whose other end is the free agent.

## Alternatives considered
| Alternative | Why rejected |
|---|---|
| Fully autonomous agent only | Unbounded variance for known procedures; hard to audit. |
| Fully scripted pipelines only | Cannot handle unknown paths — the reason to use an agent. |
| Graph frameworks with LLM-chosen edges everywhere | Moves non-determinism into control flow, where it is hardest to debug. |

## Consequences
+ Runs always terminate; replays are exact; workflows resume without repeating side effects.
− Two programming models (agent vs workflow) to document and choose between ([08](../08-determinism.md) gives the decision guide).

## Evidence
`TestWorkflows.test_journal_skips_completed_steps_on_rerun`, `test_replay_reproduces_run`, and all kernel termination tests.
