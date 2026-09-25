# ADR-004 — Single agent by default; sub-agents on demand

**Status:** Accepted · **Date:** 2026-09-24

## Context
Multi-agent systems can parallelise breadth-first work and keep exploration out of the parent's context, but cost many more tokens (~15× chat for Anthropic's research system) and add coordination failures, especially on tightly coupled work like coding.

## Decision
One agent runs every task. It may call `delegate` to spawn up to 8 fresh-context sub-agents (4 concurrently), max depth 1, with self-contained briefs, shared workspace, budget slices and aggregated usage.

## Alternatives considered
| Alternative | Why rejected |
|---|---|
| Fixed planner/executor/critic trio for every task | Pays multi-agent overhead on tasks that don't need it. |
| Unlimited recursion | Runaway cost and depth; hard to budget. |
| Separate sessions per sub-agent | Loses the single trace; parent can't account usage cleanly. |

## Consequences
+ No overhead unless delegation is chosen; one session/trace for the whole tree.
− The model must judge when to delegate; in the core suite it never did (tasks were small enough).

## Evidence
`test_delegation_runs_parallel_subagents` (+ the concurrency bug it exposed: shared shell scratch directory); live results for `hard-delegate-research` in [RESULTS](../evaluation/RESULTS.md).
