# R1 — Prebuilt agent SDKs: what they actually do (read from source, pinned versions)

> Status: **Verified against installed source** · Date: 2026-09-25 · Versions: langchain 1.4.2 · langgraph 1.2.12 · langchain-openai 1.6.6 · deepagents 0.7.19 · pydantic-ai-slim 2.49.0 · pydantic-ai-harness 0.34.0 · openai-agents 0.22.3 · openai 3.19.2 · mcp 2.2.0 · opentelemetry-sdk 1.44.0

## 1. Why this note exists

The v2 mandate is "don't build your own SDK; use prebuilt ones and enhance them where they fall short." That makes two questions empirical:

1. **Which prebuilt SDK is the best foundation?**
2. **Where exactly do the prebuilt parts fall short?** That is where Polymath's own engineering is justified.

Documentation answers neither reliably: docs describe intent, and defaults decide behaviour. Every claim below was checked in the **installed source** (file and line given where it matters) or measured hands-on against NVIDIA NIM. Claims taken only from documentation are marked *(doc)*.

## 2. Candidates

| SDK | What it is | Evaluated how |
|---|---|---|
| **LangChain v1** `create_agent` + middleware | Agent loop on LangGraph; behaviour composed from middleware hooks (`before_model`, `wrap_model_call`, `after_model`, `wrap_tool_call`) and ~19 prebuilt middlewares | source, probes, terminal bake-off (R3) |
| **deepagents** | LangChain's opinionated "deep agent" harness on the same runtime: filesystem, sub-agents, summarisation with offloading, skills, memory | source, probes, R3, stack bake-off (R5) |
| **Pydantic AI + pydantic-ai-harness** | Typed agent graph; behaviour composed from *capabilities*. The harness package adds ~40 (Coder, Shell, FileSystem, compaction tiers, ToolOutputLimits, StepPersistence, SubAgents, ConversationSearch, TrajectoryJudge, …) | source, probes, R3, R5 |
| **OpenAI Agents SDK** `SandboxAgent` | Runner + agents/handoffs; the `sandbox` package adds Filesystem, Shell (PTY "unified exec") and Compaction capabilities over local or Docker sandboxes | source, R3, R5 |

**Not evaluated, and why.** The Claude Agent SDK drives Claude models through the Claude Code runtime, and our models are NIM-hosted open-weight models. Google ADK, CrewAI, AG2 and smolagents were not tested for lack of time. That is a gap in this study, not a finding about them.

**A corrected assumption.** An earlier draft excluded the OpenAI Agents SDK because "its advanced features need the Responses API, which NIM lacks." That was never tested, and it is wrong for part of the catalogue: NIM serves `POST /v1/responses`, function calling included, for `nvidia/nemotron-3-super-120b-a12b` (HTTP 200). It returns **404 for `z-ai/glm-5.3`**. Responses support on NIM is therefore per model. The SDK is now a full candidate.

## 3. Capability matrix

✅ solid · ⚠ present, with a caveat that bites · ❌ absent · *(doc)* = taken from documentation only

| Concern | LangChain v1 | deepagents | Pydantic AI + harness | OpenAI Agents SDK |
|---|---|---|---|---|
| Agent loop | ✅ LangGraph `create_agent` | ✅ `create_deep_agent` (LangGraph) | ✅ `Agent` (pydantic-graph) | ✅ `Runner` |
| OpenAI-compatible models (NIM chat completions) | ✅ `ChatOpenAI(base_url=…)` | ✅ same | ✅ `OpenAIChatModel` + provider | ✅ chat-completions model; Responses model only where NIM serves `/responses` |
| Token usage when streaming | ⚠ **off** for any custom `base_url` unless `stream_usage=True` (`langchain_openai/chat_models/base.py:1431–1450`); first smoke run counted 0 tokens | ⚠ same | ✅ | ✅ |
| HTTP-level retries | ✅ openai SDK `max_retries` | ✅ same | ✅ tenacity transports (`pydantic_ai.retries`) | ✅ opt-in `ModelRetrySettings` + policies |
| Error reported **inside a 200** (NIM's first SSE event `{"error":"Service temporarily overloaded"}`) | ✅ `ModelRetryMiddleware` retries any exception, ⚠ **but default `on_failure="continue"` turns an exhausted retry into an ordinary AI message**, so the run looks `completed` (seen in the first bake-off) | ⚠ same | ❌ transports retry on HTTP status only; the stream error surfaces at `request_stream`'s first-chunk peek and kills the run (smoke runs) | ⚠ depends on policy; not isolated |
| **Account-wide** rate governance | ❌ per-client only | ❌ | ❌ | ❌ |
| Malformed tool arguments | ✅ `ToolRetryMiddleware` / error returned to model | ✅ | ✅ `RepairToolArguments` (in `Coder`) | ❌ sandbox tools: validation error → `UserError` aborts the run (`run_internal/tool_execution.py:1845`; seen in smoke run) |
| Clear old tool results | ✅ `ContextEditingMiddleware(ClearToolUsesEdit)` | via summarisation | ✅ `ClearToolResults` (Coder: at 70 % of window) | ❌ client-side; server-side only |
| Summarisation | ✅ `SummarizationMiddleware` | ✅ always on; history **offloaded** to `/conversation_history/{id}.md` *(doc)*; `compact_conversation` tool | ✅ `SummarizingCompaction` (incremental, receipts, cross-model bridge), `TieredCompaction` orchestrator | ⚠ `Compaction` capability only sends `context_management:[{type:"compaction"}]` (`sandbox/capabilities/compaction.py:188–208`); works only where the **server** implements it |
| Summarisation trigger fits the model | ⚠ configurable | ⚠ without a LangChain model profile (true for **every NIM model** we checked) it falls back to **170 000 tokens, keep 6 messages** (`middleware/summarization.py:262–300`), which is above the 128 k window of gpt-oss-20b, Gemma 4 and Nemotron Lightning | ✅ fraction of the resolved window | n/a |
| Large outputs made recoverable at production time | ❌ | ⚠ large results offloaded to files | ✅ `ToolOutputLimits` `Spill`: handle + preview + bounded `read_tool_result` | ❌ output unbounded by default (R3) |
| Old results recoverable after eviction | ❌ | ⚠ via offloaded history file | ❌ **documented**: blanked results are gone; the persisted run holds the compacted history | ❌ |
| Recall over history | ❌ | ⚠ read the offload file | ✅ `ConversationSearch` (BM25 over persisted steps) | ❌ |
| Pinning | ❌ | ❌ | ✅ `pin()` (manual) | ❌ |
| Prompt-cache awareness | provider middleware | ✅ Anthropic prompt-caching middleware | ⚠ `min_clear_tokens` threshold, `WarnOnCacheBusts` | n/a |
| Durable state / resume | ✅ LangGraph checkpointers (SQLite, Postgres) | ✅ | ✅ `StepPersistence`; durable-exec integrations *(doc)* | ✅ Sessions (SQLite, …), `RunState` |
| Sub-agents | via deepagents | ✅ `task` tool, fresh context | ✅ `SubAgents` | ✅ handoffs, agents-as-tools |
| Planning | ✅ `TodoListMiddleware` | ✅ opt-in | ✅ `Planning` (survives compaction) | ❌ not found |
| Shell tool (R3, 14 scenarios) | 7/14 | 9/14 | 10/14 (`Shell`) · 10/14 (`Coder`) | 7/14, plus a cwd bug |
| Trajectory self-evaluation | ❌ | ⚠ rubric grading (beta) *(doc)* | ✅ `TrajectoryJudge` | ❌ (guardrails are policy, not quality) |
| MCP | adapter package *(doc; not installed here)* | adapter package *(doc)* | ✅ capability | ✅ `mcp_servers` |
| Observability | LangSmith (installed: langsmith 0.14.0) | same | ✅ native OpenTelemetry | tracing processors (default exporter: OpenAI) |

## 4. Defects and hazards found (each reproducible)

These are the concrete places where "use it as shipped" produces a wrong result or a lost run, which is precisely where enhancement is justified.

| # | SDK | Finding | Evidence | Severity for autonomous agents |
|---|---|---|---|---|
| D1 | openai-agents 0.22.3 | Any command containing a background `&` runs in **`/`**, not the workspace | R3 §5.6; `unix_local.py:905` | High: files written to the filesystem root |
| D2 | openai-agents 0.22.3 | Malformed tool arguments to sandbox tools abort the whole run | smoke run; `tool_execution.py:1845` | High: one model slip ends the task |
| D3 | langchain 1.4.2 | `ModelRetryMiddleware(on_failure="continue")` (the default) turns an exhausted retry into a normal AI message | first bake-off; `model_retry.py:121` | High for automation: failures look like completions |
| D4 | langchain-openai 1.6.6 | No streamed token usage for custom `base_url` unless `stream_usage=True` | smoke run (0 tokens); `base.py:1431` | Medium: cost accounting silently zero |
| D5 | pydantic-ai 2.49 | Documented retry transports can't see errors reported inside a 200 stream | smoke runs; trace ends in `openai/_streaming.py:217` | High on NIM: overload kills the run |
| D6 | deepagents 0.7.19 | Summarisation falls back to a 170 k trigger for profile-less models, above many models' windows | `summarization.py:292–300` | High for 128 k models: summarisation can't fire before overflow (by arithmetic; not reproduced live) |
| D7 | deepagents 0.7.19 | Shell output truncated head-first (tail lost); timed-out commands leave orphans | R3 §5.2 | Medium |
| D8 | langchain 1.4.2 | Shell timeout restarts the session after a 10 s grace and discards all partial output | R3 §5.1; `shell_tool.py:186–215, 300–305` | Medium |
| D9 | openai-agents 0.22.3 | `Compaction` is server-side only; no client fallback | `compaction.py:188–208` | High off OpenAI: long runs have no compaction |
| D10 | pydantic-ai-harness 0.34 | `ClearToolResults` is irreversible; `Spill` covers only outputs that were large when produced | harness `compaction/README.md` §receipts | Medium: forces re-execution, which may not reproduce |

## 5. What this implies for v2

1. **Context engineering.** Pydantic AI's harness has the most complete prebuilt toolkit by a clear margin: clearing, dedup, clamping, incremental summarisation with receipts, a tiered orchestrator, pinning, spill-with-handles, BM25 recall and step persistence. Crucially, its `TieredCompaction` accepts any object with `async def compact(messages, ctx)` as a tier. Polymath's context research (R2) can therefore ship as **additional tiers inside the prebuilt orchestrator**, not as a replacement.
2. **Resilience belongs below the SDK.** Four of the ten findings (D2–D5) are resilience failures, and none of the four SDKs governs rate at the account level. The egress governor (R4) fixes D3–D5 and the rate-limit storms for every SDK at once, because it sits under all of them.
3. **The terminal stays Polymath's.** R3: no prebuilt shell exceeds 10/14, and D1, D7 and D8 are silent-corruption or lost-information failures.
4. **Foundation choice** waits for the controlled stack bake-off (R5). This note shows capability breadth; R5 measures what the capabilities achieve on real tasks.
