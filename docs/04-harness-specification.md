# 04 — Harness Specification

> Status: **Normative** — this document specifies behaviour the implementation must preserve. Changes require an ADR.
> Code: `polymath/kernel/agent.py`, `polymath/events.py`, `polymath/context/conversation.py` · Schemas: [`spec/schemas/`](../spec/schemas)

The *harness* is everything around the model that turns it into an agent: the loop, the journal, the context projection, tool execution, budgets, verification and recovery. This document is its contract.

---

## 1. Vocabulary

| Term | Definition |
|---|---|
| **Session** | One directory under `sessions_dir` holding one event log, one trace file and spill files. Id: `YYYYMMDDTHHMMSSZ_<8 hex>` or caller-supplied. |
| **Agent** | One loop instance identified by an **agent id**: `main`; sub-agents `main.1`, `main.2`, `main.1.1`; workflow agents `wf.<step>.<n>`. Many agents may share one session log. |
| **Turn** | One model call and the execution of the tool calls it returned. |
| **Event** | One immutable, sequenced fact appended to the log. |
| **Projection** | The pure function `project(events, agent) → ConversationState`. |
| **View** | The message list sent to the model: `materialize(state)` after clearing/compaction events are applied. |

---

## 2. Kernel invariants

| ID | Invariant | Enforcement | Test |
|---|---|---|---|
| **I1** | *Journal before act.* A model response is appended before any of its tool calls runs; each tool result is appended before the next model call. | `_loop`: `append(MODEL_RESPONSE)` precedes `_execute`; `_execute` appends each result. | `test_kill_dash_nine_then_resume` |
| **I2** | *Pairing.* Every tool call in the view is followed by exactly one result before the next assistant message; no orphan results. | `materialize` synthesises results for interrupted calls and drops orphaned results; compaction cuts only at turn boundaries. | `test_context.assert_wire_valid` (all context tests), `test_agent_survives_every_fault_kind` |
| **I3** | *Termination.* Every iteration either consumes budget (a model call counts a turn) or returns. | Budget check at loop top; empty/prose counters; wrap-up is a single call. | `test_turn_budget_wrap_up`, `test_empty_responses_fail_cleanly` |
| **I4** | *Isolation of faults.* Tool exceptions become error results; model failures end the run with `failed` and a resumable log. | `ToolRegistry.execute` try/except; `ModelError` handling in `_loop`. | `test_crashing_tool_is_captured`, `test_model_error_fails_cleanly` |
| **I5** | *Deterministic ordering.* Results are journaled in call order even when executed concurrently; tool specs are sorted by name. | `execute_batch` fills an index-addressed list; `ToolRegistry.specs()` sorts. | `test_parallel_batch_preserves_order` |
| **I6** | *Pure view.* The model's view depends only on the event log (plus the static system prompt and tool list recorded in `task.submitted`). | No mutable conversation object exists; the kernel re-projects every turn. | `test_resume_executes_pending_calls`, `test_replay_reproduces_run` |
| **I7** | *Stable prefix.* The system prompt contains no task-, time- or path-specific data. | `build_system_prompt` takes only the skills index; env snapshot goes to the task message. | `test_happy_path` (asserts workspace path absent from system prompt) |

---

## 3. Session storage layout

```text
<sessions_dir>/<session_id>/
├── events.jsonl          # the journal (source of truth)            — spec/schemas/event.schema.json
├── trace.jsonl           # OTLP-shaped spans                         — spec/schemas/span.schema.json
├── NOTES.<agent>.md      # durable scratchpad per agent (notes tool)
├── outputs/<call_id>.txt # full text of clipped tool outputs (spill files)
└── shell/<agent>/        # transient per-agent command scripts (deleted after each command)
```

Defaults: `sessions_dir = ~/.polymath/sessions`; long-term memory at `~/.polymath/memory.jsonl`. Both relocatable via config/env (`POLYMATH_HOME`, `POLYMATH_SESSIONS_DIR`).

### 3.1 Event log format and guarantees

- JSON Lines, UTF-8, one event per line: `{"seq", "ts", "type", "agent", "data"}`.
- `seq` is assigned under a process-wide lock per log: **dense, strictly increasing, starting at 1**.
- Each append is written and `flush()`ed before `append` returns; with `fsync_events = true` it is also `fsync`ed (durable against power loss, ~1 ms cost per event).
- **Torn-tail repair:** on open, a final line without a trailing newline (crash mid-write) is truncated before appending. A corrupt line anywhere *else* is a hard error — the log is never silently rewritten.
- Listeners (console, custom sinks) are invoked after the write, outside the lock; listener exceptions are swallowed.

---

## 4. Event catalogue

| Type | Emitted by | `data` payload | Consumed by projection |
|---|---|---|---|
| `session.started` | kernel (main, once) | `{model, mode, config (public), version}` | — |
| `task.submitted` | kernel | `{task: TaskSpec, system_prompt, tools[], model, profile}` | `state.task` |
| `task.state` | kernel | `{state, reason?}` | `state.state` |
| `message.user` | kernel | `{content, source: task\|harness\|verifier\|user}` | user message; `source=task` pins it |
| `model.request` | (reserved) | `{model, n_messages, est_tokens, request_hash}` | — |
| `model.response` | kernel | `{response: ModelResponse, est_input_tokens, wrap_up?}` | assistant message; usage; turns |
| `model.error` | kernel | `ModelError.to_dict()` | — |
| `tool.result` | kernel | `{result: ToolResult}` | tool message |
| `context.cleared` | context engine | `{upto_seq, cleared, est_saved_tokens, before_tokens}` | stub tool results with `seq ≤ upto_seq` |
| `context.compacted` | context engine | `{upto_seq, summary, method: llm\|fallback, summarized_entries, before_tokens, duration_s}` | replace entries `≤ upto_seq` by summary |
| `plan.updated` | `todo` tool | `{items[]}` | `state.plan` |
| `verification.result` | kernel | `{verification: {passed, method, detail, round}}` | `state.verifications` |
| `subagent.spawned` | kernel (parent) | `{child_agent, task}` | — |
| `subagent.finished` | kernel (parent) | `{child_agent, result: RunResult}` | — |
| `workflow.step.started` | workflow engine | `{step}` | — |
| `workflow.step.completed` | workflow engine | `{step, output}` | workflow journal (resume) |
| `task.completed` | kernel | `{result: RunResult}` | `state.completed` |
| `harness.note` | kernel | `{kind: resumed\|loop_detected\|error_streak\|budget_exhausted\|workflow_failed, detail}` | — |

The set of types is validated against `event.schema.json` by `tests/test_specs.py::test_event_types_in_code_match_spec`.

---

## 5. Projection and view (normative algorithm)

```text
project(events, agent):
  for e in events where e.agent == agent (in seq order):
    task.submitted      → state.task = e.task
    message.user        → entries += user(e.content); first with source=task becomes task_entry
    model.response      → entries += assistant(content, tool_calls); usage += e.usage; turns += 1
    tool.result         → entries += tool(output, call_id)
    context.cleared     → cleared_upto = max(cleared_upto, e.upto_seq)
    context.compacted   → compaction = {upto_seq, summary}          # latest wins
    plan.updated        → plan = e.items
    verification.result → verifications += e
    task.completed      → completed = e.result

materialize(state):
  view = [task_entry]
  if compaction: view += user("[Context summary …]" + compaction.summary)
  for entry in entries after compaction.upto_seq (excluding task_entry):
     tool result whose call is not open         → drop (orphan)
     tool result with seq ≤ cleared_upto, > clear_min_chars → first 300 chars + "[Output cleared …]"
     assistant                                   → close any still-open calls with synthetic results; open its calls
     user                                        → close open calls; append
  close any calls still open with synthetic results
```

---

## 6. Resume semantics

`Agent.resume(runtime, log, agent_id)`:

1. Project the agent's events. If `task.completed` exists → return the recorded `RunResult` (idempotent).
2. Restore the **original system prompt** from `task.submitted` (not the current code's prompt) and the plan from the last `plan.updated`.
3. Compute *pending calls* = tool calls of the last assistant message without results. Re-execute them (**at-least-once**), journal results.
4. If a pending `finish` was among them → go through completion/verification.
5. Continue the normal loop. Budgets are measured from the log (turns, tokens) and from the first event's timestamp (wall time).

**Idempotency guidance for tool authors.** Because a call interrupted between execution and journaling runs again on resume, tools should be idempotent where possible (`write_file` and `edit_file` are; a `bash` command such as `echo x >> log` is not). The crash test documents this explicitly: the interrupted command runs twice.

---

## 7. Replay semantics

`Runtime.replay(session_id, workspace=…, strict=False)` re-runs the task with a `ReplayClient` that serves the recorded `model.response` events in order, while **tools execute for real**. Uses:

- **Regression test for the harness:** change kernel/tool code, replay old sessions, diff tool outputs.
- **Debugging:** reproduce a failure deterministically without model cost.
- `strict=True` compares each request's content fingerprint with the recording and raises `ReplayDivergence` at the first difference, pinpointing where behaviour changed.

For an exact byte-level record/replay of every exchange (including compaction and judge calls), wrap any client in `RecordingClient(client, cassette)` and replay with `ReplayClient(cassette, strict=True)`.

---

## 8. Concurrency model

| Resource | Concurrency rule |
|---|---|
| Event log | Many writers (agents in threads); appends serialised by an `RLock`; seq dense. |
| Tool batch | Consecutive parallel-safe calls run in a `ThreadPoolExecutor(tool_parallelism)`; others sequential. |
| Sub-agents | `ThreadPoolExecutor(max_parallel_subagents)`; each child has its own `ToolContext`, shell and shell scratch dir. |
| Shell | One command at a time per `PersistentShell` (lock). |
| Model clients | Shared across threads; `OpenAICompatClient` holds no per-request state; breakers are locked. |
| Workspace | **Shared** by parent and children — delegation briefs must assign disjoint output files (stated in the `delegate` tool description). |

---

## 9. Extension points

| Extend | How | Contract |
|---|---|---|
| New tool | Subclass `Tool` (name, description, JSON-schema `parameters`, `run(args, ctx)`), register in `build_registry` or at runtime `registry.register(tool)` | Return `str` or `ToolOutput`; raise `ToolError` for model-actionable failures; mark `parallel_safe` only if read-only and thread-safe. |
| New skill | Add `<dir>/<name>/SKILL.md` with `name`/`description` front matter; list the dir in `skill_dirs` | Body is loaded on demand; keep descriptions ≤ 1 line. |
| New model provider | Any OpenAI-compatible endpoint via `base_url`; or implement `ModelClient.complete(ChatRequest) → ModelResponse` | Raise `ModelError` with a kind from `gateway/base.py`. |
| New workflow | Compose `Step`/`Loop` in Python; register in `WORKFLOWS` | Side effects only through `ctx.llm/agent/shell`; JSON-serialisable outputs. |
| Event sinks | `EventLog.subscribe(fn)` | Must not block; exceptions ignored. |
| Verification | `verify_command` per task, or `acceptance_criteria` for the judge | Command must be deterministic and idempotent. |

---

## 10. Versioning

- The **event schema** is additive-only within a major version: new event types and new optional fields are allowed; renames/removals require a major bump and a migration note.
- `session.started.data.version` records the harness version that wrote the log; resume across versions is supported within a major version.
