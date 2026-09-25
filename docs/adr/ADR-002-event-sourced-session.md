# ADR-002 — The session is an append-only event log; the context is a projection

**Status:** Accepted · **Date:** 2026-09-24

## Context
Agents run for minutes to hours against unreliable providers. We need crash recovery, reproducibility for debugging, auditability, and tracing — ideally without four separate mechanisms.

## Decision
Every fact is appended to `events.jsonl` **before** it influences anything (journal-before-act). The model's context is recomputed each turn by a pure projection of the log. Context rewrites (clearing, compaction) are themselves events.

## Alternatives considered
| Alternative | Why rejected |
|---|---|
| In-memory message list, snapshot periodically | Loses work between snapshots; snapshots and live state can diverge. |
| Database (SQLite/Postgres) | Adds a dependency and a schema-migration burden for what is an append-only stream. |
| External durable-execution engine (Temporal etc.) | Excellent, but heavy for a single-process agent; our journal gives the same resume semantics for this scope. |

## Consequences
+ Resume = re-project + re-execute pending calls; replay = serve recorded responses; the log *is* the audit trail.
+ The view is reproducible (compaction decisions are recorded, not recomputed).
− At-least-once tool execution on resume: tools should be idempotent where possible.
− Projection is O(events) per turn (negligible at agent scales: thousands of events).

## Evidence
`test_kill_dash_nine_then_resume` kills the process group with SIGKILL mid-tool and resumes to completion; `test_torn_tail_is_repaired`; `test_concurrent_appends_are_dense_and_whole` (8 threads × 250 events).
