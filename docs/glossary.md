# Glossary

| Term | Meaning in Polymath |
|---|---|
| **A2A** | Agent2Agent protocol (Linux Foundation, v1.0 2026): task lifecycle and discovery between agents. Polymath's task states follow its vocabulary. |
| **Ablation** | Running the same tasks with a feature removed (here: `--mode minimal`) to measure what the feature contributes. |
| **Agent id** | Identifier of one loop instance within a session: `main`, `main.1` (sub-agent), `wf.fix.1` (workflow agent). |
| **At-least-once** | Resume re-executes tool calls that were started but not journaled; such calls may run twice. |
| **Budget** | Hard ceilings per task: turns, tokens (incl. sub-agents), wall-clock seconds (active time), tool calls. |
| **Cache-shape discipline** | Keeping the prompt prefix byte-stable across turns and tasks so provider/KV caches hit. |
| **Circuit breaker** | Per-model switch that stops sending requests to a failing model for a cool-down period. |
| **Clearing** | Replacing old, large tool outputs in the view with short stubs (recorded as `context.cleared`). |
| **Code mode** | The model writes a program that performs many steps, instead of many tool calls; intermediate data stays out of context. |
| **Compaction** | Summarising older turns into a structured summary (recorded as `context.compacted`). |
| **Delegation** | The `delegate` tool: parallel sub-agents with fresh contexts returning condensed reports. |
| **Event log / journal** | `events.jsonl`: the append-only source of truth for a session. |
| **Finish** | The explicit tool call that ends a task with an answer and artifacts. |
| **Harness** | Everything around the model that makes it an agent: loop, tools, context engine, journal, budgets, verification, recovery. |
| **Hidden verifier** | Evaluation checker the agent never sees. |
| **Journal-before-act** | Invariant I1: record a model response before executing its tool calls. |
| **Materialise** | Turn a projected conversation state into the list of messages sent to the model. |
| **MCP** | Model Context Protocol (spec 2026-07-28, stateless core): standard for exposing tools/resources to models. |
| **Minimal mode** | Ablation baseline: 5 tools, no skills/hints/memory/nudges/verification. |
| **Nudge** | A short harness message steering the model (e.g. "call finish or continue"). |
| **Parallel-safe** | A read-only, thread-safe tool that may run concurrently with others in the same turn. |
| **Playbook / skill** | A `SKILL.md` document of procedural know-how, loaded on demand (or inlined when routing is confident). |
| **Projection** | Pure function from the event log to conversation state. |
| **Replay** | Re-running a session with recorded model responses and real tools. |
| **Router** | Intake classifier producing a task profile (category, complexity, hints). |
| **Session** | A directory with one event log, one trace and spill files; may contain many agents. |
| **Spill file** | Full text of a clipped tool output, saved under `outputs/` and referenced in the clipped result. |
| **Stop reason** | Why a run ended: finished, text_answer, finished_unverified, budget_exhausted, model_error, no_progress, crash. |
| **Text protocol** | Tool calling via `<tool_call>{json}</tool_call>` text blocks, for models without native tool calling. |
| **Turn** | One model call plus execution of its tool calls. |
| **Utility model** | A cheaper model used for compaction, judging and optional routing. |
| **Workflow** | Code-defined control flow with LLM/agent leaves; steps journaled and skipped on re-run. |
