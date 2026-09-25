# 00 — Vision & Engineering Principles

> Status: **Adopted** · Audience: everyone building, extending or operating Polymath

## 1. Vision

**Polymath is a generalist agent**: give it a task in plain language and a workspace, and it completes the task end-to-end — writing and testing code, debugging, analysing data, operating the terminal, researching documents and the web, producing written deliverables — then reports what it did and how it verified it.

The model supplies judgement. **The harness supplies everything that makes judgement reliable**: a real environment to act in, a memory that survives any context window, bounded and resumable execution, verification, and complete observability. The quality bar is engineering-grade: every behaviour is specified, tested (offline and live) and measurable.

## 2. Goals

| ID | Goal | Measured by |
|---|---|---|
| G1 | Complete diverse real tasks autonomously | Pass rate on the 28-task, 11-category live suite ([13](13-evaluation-and-testing.md)) |
| G2 | Never lose work | Crash at any instant → `resume` continues (tested with real `kill -9`) |
| G3 | Always terminate | Every run ends in ≤ budget with a typed `stop_reason` |
| G4 | Be reproducible | Replay of a recorded session reproduces the tool trajectory |
| G5 | Be model-agnostic | Any OpenAI-compatible endpoint; native or text tool protocol; fail-over chain |
| G6 | Be observable | Every decision in `events.jsonl`; every call a span |
| G7 | Stay cheap in context | Cache-stable prefix; clearing & compaction keep prompts bounded |
| G8 | Zero dependencies | Standard-library Python only |

## 3. Non-goals (v1)

- A GUI. (The CLI, Python API and trace files are the interfaces.)
- Training or fine-tuning models.
- Hosting models. Polymath consumes endpoints.
- Being a framework of abstractions. It is a *working agent* with a small, sharp core.

## 4. Engineering principles

These are the tie-breakers used in every design decision in this repository.

1. **Simple loop, rich environment.** The kernel is a plain `while` loop. Capability comes from tools and context, not from control-flow cleverness. (Anthropic, *Building effective agents*, 2024: "the most successful implementations use simple, composable patterns".)
2. **The log is the truth.** State is never held only in memory. If it is not in `events.jsonl`, it did not happen; if it is, it can be replayed.
3. **Deterministic harness, stochastic model.** Put every decision that *can* be code into code: budgets, termination, context rewrites, verification commands, workflow control flow. Leave the model only the decisions that need judgement.
4. **Context is a budget.** Find "the smallest set of high-signal tokens that maximise the likelihood of the desired outcome" (Anthropic, *Effective context engineering*, 2025). Load just-in-time; clear before you compact; compact before you truncate.
5. **Errors are information.** Every failure — bad JSON, unknown tool, timeout, crash, verification failure — returns to the model as an actionable message. Nothing fails silently; nothing crashes the loop.
6. **Explicit over implicit.** Completion is an explicit `finish` call; plans are explicit `todo` lists; long-term facts are explicit `notes`/`memory` writes.
7. **Measure, then believe.** Features earn their place through evals and ablations, and results are reported as measured, including the unflattering ones ([evaluation/RESULTS.md](evaluation/RESULTS.md)).
8. **Stable prefixes.** The system prompt and tool list are byte-identical across tasks; task-specific data goes after them. This is what makes provider prompt caching work.
9. **Small tool surface, orthogonal tools.** 14 tools, no overlap. Each description says when to use it, what it returns and its limits.
10. **Everything testable offline.** Any model interaction can be scripted, recorded or replayed, so the whole harness is covered by fast deterministic tests.

## 5. Design lineage (what we built on)

| Idea | Source | Where in Polymath |
|---|---|---|
| Workflows vs agents; augmented LLM; evaluator–optimiser; orchestrator–workers | Anthropic, *Building effective agents* (2024) | [08](08-determinism.md), `kernel/workflows_builtin.py`, `delegate` |
| Context rot, attention budget, compaction, structured note-taking, sub-agents returning condensed summaries | Anthropic, *Effective context engineering for AI agents* (2025) | [05](05-context-engineering.md), `notes`, `delegate` |
| Session / harness / sandbox decoupling ("brain vs hands"), `wake(sessionId)` resume from an event log | Anthropic, *Managed Agents* engineering post (2026) | Event-sourced session, `Agent.resume` |
| Initializer + incremental sessions, progress files, JSON feature lists | Anthropic, *Effective harnesses for long-running agents* (2025) | `todo`, `notes`, compaction summary schema |
| Orchestrator–worker research, token usage explains most variance | Anthropic, *How we built our multi-agent research system* (2025) | [10](10-multi-agent-orchestration.md) |
| Durable execution: journal each step, replay to recover | Temporal / Restate / DBOS | Event log + workflow journal |
| Stateless protocol core, Tasks extension, `CacheableResult` | MCP spec 2026-07-28 | Tool mapping in [06](06-tool-system.md#8-mcp-mapping) |
| Task lifecycle states, Agent Cards | A2A v1.0 (Linux Foundation, 2026) | Task states, [10 §7](10-multi-agent-orchestration.md#7-a2a-mapping) |
| `invoke_agent` / `chat` / `execute_tool` spans | OpenTelemetry GenAI semantic conventions | [12](12-observability.md) |
| Terminal as the evaluation surface | Terminal-Bench 2.x | [07](07-terminal-subsystem.md) |
| Orchestration design dominates token economics | *The Harness Effect* (arXiv 2607.06906, 2026) | [15](15-performance-and-cost.md) |
