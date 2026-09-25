# ADR-009 — Harness features must pay for their tokens

**Status:** Accepted · **Date:** 2026-09-25

## Context
The v1 ablation on GLM-5.3 (core suite, 28 tasks) found **both** the full harness and a minimal harness (5 tools, no skills/hints/memory/nudges/verification) at 28/28 — but the full harness used **2.1× the tokens** (1.27 M vs 0.60 M): +48 % turns (7.4 vs 5.0 mean) and +50 % input tokens per call. Sources: standalone `load_skill` turns (34), `todo` turns (13), 4 confirmation nudges on replies that were already final, and ~2.9k tokens of tool specs per call.

## Decision (v1.1)
1. Accept final-looking prose immediately (ADR-007 amendment).
2. Inline the top skill into the task message when routing is confident, instead of hinting it (saves the `load_skill` turn); emit skill hints only on a clear keyword winner.
3. Tighten tool descriptions and schemas (−29 % spec size, all behavioural rules kept).
4. Ask for the plan to be sent in the same turn as the first actions; no plan for simple tasks.
5. Keep every feature whose value shows up on hard or long tasks (verification, compaction, notes, delegation) — and measure it there, not assume it.

## Consequences
Harness features now carry an explicit burden of proof: a feature that does not improve pass rate, robustness or cost on some measured slice is removed or made conditional. Results before/after are in [RESULTS](../evaluation/RESULTS.md).
