# 16 — Roadmap

> Everything here is **not implemented**. Items are ordered by expected value per unit of effort, informed by the evaluation results.

## Next (v1.3)

| Item | Why | Design sketch |
|---|---|---|
| **MCP client adapter** | Access to thousands of existing tool servers | One `Tool` per remote tool from `tools/list`; `tools/call` over stateless Streamable HTTP (spec 2026-07-28); cache lists by `ttlMs`; `InputRequiredResult` → `ask_user` |
| **Adaptive profile selection** | Minimal harness is cheaper on simple tasks with strong models | Router picks `minimal` toolset for low-complexity tasks; full set otherwise; measure pass/tokens on both tiers |
| **macOS support for the terminal** | `/proc`-based descendant tracking is Linux-only | `pgrep -P` / `ps -o pid,ppid` fallback |

## v2: implemented (see [18](18-v2-architecture.md))

"v2" became the re-platform onto a prebuilt SDK, the Pydantic AI harness, with Polymath's measured enhancements swapped in. Several items that used to sit on this roadmap now come prebuilt: MCP (a Pydantic AI capability), OpenTelemetry instrumentation (native), and cost accounting (`genai-prices`).

| Next for v2 | Why |
|---|---|
| **ADR-011 and defaults from data** | Choose the foundation and the context defaults from R5 and the retention arms, not from design intent |
| **PTY session tool (opt-in)** | Interactive programs (REPLs, prompts). The Agents SDK's `write_stdin` idea, kept off by default so accidental prompts still fail fast (R3) |
| **Upstream reports** | File the reproducible SDK defects (R1 D1–D10) with their reproductions |
| **LangChain adapter for the Polymath tiers** | `RecallableEviction` / `StateLedger` as LangChain middleware, so the enhancements aren't tied to one SDK |

## Later

| Item | Why |
|---|---|
| **A2A server & client** | Expose Polymath as an A2A v1.0 agent (Agent Card from tools + skills; task states already aligned); `delegate` backend that targets remote agents |
| **Remote hands** | Run tools in a separate sandbox service (microVM per session) with the brain elsewhere; `execute(name, input) → string` over RPC |
| **Embedding-backed memory** | BM25 is adequate for hundreds of memories; embeddings for thousands, with BM25 hybrid |
| **Skill learning** | After a successful long task, distil a candidate `SKILL.md` for human review |
| **Evaluation expansion** | Repeats (`--repeat 5`) on every model for variance; public benchmark adapters (Terminal-Bench tasks run through the same runner) |
| **OTLP exporter** | Direct export of `trace.jsonl` spans to an OTel collector (currently a trivial offline conversion) |
| **Cost accounting** | Per-model price table → dollars in `RunResult` |

## Explicitly out of scope

GUI; model training/fine-tuning; hosting models.
