# ADR-011 — v2 foundation: Pydantic AI + pydantic-ai-harness

**Status:** Accepted · **Date:** 2026-09-25 · Supersedes [ADR-006](ADR-006-stdlib-only-core.md) for v2 (the v1 core stays standard-library only) · Research: [R1](../research/01-sdk-landscape.md), [R3](../research/03-terminal-bakeoff.md), [R5](../research/05-stack-bakeoff.md), [R6](../research/06-retention.md)

## Context
The v2 mandate: don't build an SDK, use prebuilt ones, and enhance them where they measurably fall short. Four candidates were audited in source (R1), benchmarked on the same 28 tasks, model and hidden verifiers (R5), and compared on the component that matters most, the shell (R3).

## Decision
Build v2 on **Pydantic AI 2.49 + pydantic-ai-harness 0.34**, starting from the harness's `Coder` composition and swapping in Polymath components only where measurement justified it (ADR-012/013/014).

## Evidence, by criterion

| Criterion | LangChain / deepagents | **Pydantic AI + harness** | OpenAI Agents SDK |
|---|---|---|---|
| Task outcomes, core suite, `nemotron-3-super`, repeat 1 (R5 §5.2) | deepagents: see R5 table | Coder: see R5 table | see R5 table |
| Context-engineering toolkit (R1 §3) | clearing, summarisation (offload in deepagents) | **the most complete**: clearing, dedup, clamping, incremental summarisation with receipts, tiered orchestrator, pinning, spill-with-handles, BM25 recall, step persistence | server-side compaction only (D9) |
| Extension surface for Polymath's enhancements | middleware hooks | **capability hooks + `CompactionStrategy` tiers**; v2 swaps parts out of a *real* `Coder`, so every unswapped part is exactly as shipped | limited: sandbox capabilities, runner hooks |
| Defects that affect autonomous runs on NIM (R1 §4) | D3 fake completions (config fix), D4 zero token counts (config fix), D6 170 k summarisation trigger above 128 k windows, D7/D8 shell | D5 in-body errors not retried (**fixed below the SDK** by the governor, ADR-012); D10 irreversible clearing (**fixed** by ADR-014's tier) | **D1 commands with `&` run in `/`**, D2 malformed tool arguments abort the run, D9 no client-side compaction |
| Model reach on NIM | chat completions, any model | chat completions, any model | Responses API **per model** (Nemotron yes, GLM-5.3 404); chat-completions path exists but is second-class |
| Shell as shipped (R3) | 7/14 (LangChain), 9/14 (deepagents) | 10/14 (`Shell`), 10/14 (`Coder`) | 7/14 + D1 |

## Alternatives considered
- **deepagents.** The strongest alternative. Its task outcomes are comparable (R5), and it brings LangGraph's durable checkpointing and a large ecosystem. It lost on two counts. First, its context toolkit is narrower, and its defaults for profile-less models (every NIM model) are unsafe (D6). Second, more of its defects sit *inside* the loop (D3, D4), where only configuration discipline, not a component swap, protects a deployment. Polymath's tiers could be ported to LangChain middleware (roadmap).
- **OpenAI Agents SDK.** Rejected for NIM deployments. D1 writes to the filesystem root, D2 turns a single malformed tool call into a failed run, and compaction exists only where the server implements it.
- **Stay on v1 (own stack).** Rejected by the mandate, and not supported by the evidence either: on the same model and tasks the prebuilt stacks matched or beat v1 (R5), and v1's distinctive strengths (terminal, verification discipline, event log) are carried into v2 as components.

## Consequences
- \+ v2 inherits a maintained loop, model adapters, instrumentation (OpenTelemetry-native), MCP and durable-execution integrations without owning them.
- \+ Every Polymath enhancement is an ablation against the unmodified prebuilt agent (`V2Options` flags). Claims about v2 are paired comparisons, not anecdotes (R5 §5.1, R6).
- − Pinned to pre-3.0 Pydantic AI APIs (deprecations already visible, e.g. httpx → httpx2). The pins live in `pyproject.toml` extras, and `tests/test_v2.py` drives a real agent, so upgrade breakage surfaces in tests.
- − v2 depends on the **structure** of `Coder`'s capability list: it swaps parts by type. A harness release that restructures `Coder` fails `test_all_swaps_off_is_exactly_coder` (`tests/test_v2.py`), which pins that the all-off build equals `Coder` part for part, instead of silently skewing the ablations.
