# 15 — Performance & Cost

> Status: **Measured** on NVIDIA NIM, September 2026 · Raw data: `evals/results/*/results.json`

## 1. Where the time goes

Measured from the trace spans of the 28 v1 GLM-5.3 core sessions:

| Component | Share of wall time (median) | Notes |
|---|---|---|
| Model calls (`chat` spans) | **99.8 %** | queueing + prefill + generation on a shared endpoint |
| Tool execution (`execute_tool`) | 0.2 % | builds, tests and scripts in these tasks are fast |
| Harness (projection, context prep, journaling, validation) | **~1.6 ms per turn** (max 2.7 ms) | ≈ 13 ms per whole session |

The harness is effectively free; **every performance lever is about the model**: fewer turns, smaller prompts, better cache reuse, and less queueing.

Model latency per call during the v1 run (4 concurrent sessions): p50 5.2 s, p90 35 s, max 213 s (GLM-5.3). Shared-endpoint latency is dominated by load, not by prompt size.

## 2. Token economics

Total tokens ≈ Σ over turns of (prefix + history + output). History is re-sent each turn, so **turn count is the largest multiplier**, then **per-call prefix size**.

| Lever | Mechanism in Polymath | Measured effect |
|---|---|---|
| Fewer turns | parallel read-only calls in one turn; accept final-looking prose; inline confidently-routed skill; plan in the same turn as first actions | v1.1 vs v1: see §3 |
| Smaller prefix | tight tool descriptions (−29 % spec size, ~850 tokens/call); task data outside the system prompt | ~850 tokens × every call |
| Smaller history | tool-output clipping (24k chars) + spill files; batched clearing; compaction | bounded context on long tasks |
| Cache reuse | byte-stable system prompt + sorted tools; append-only history between rewrite events | NIM reported 122,496 cached tokens in one Nemotron session (38 % of its 322k input tokens) |
| Code mode | scripts keep intermediate data out of context | 3,000-row CSV analysed with max tool output 1.6k chars |
| Cheaper model for chores | utility model for compaction and judging | – |

## 3. The ablation that shaped v1.1

On the core suite with GLM-5.3, the v1 full harness and a minimal harness both passed 28/28, but:

| | v1 full | minimal | ratio |
|---|---|---|---|
| Total tokens | 1,269,061 | 595,323 | 2.13× |
| Model calls | 206 | 140 | 1.47× |
| Mean turns / task | 7.4 | 5.0 | 1.48× |
| Input tokens / call | 5,734 | 3,834 | 1.50× |
| First-call input (median) | 3,615 | 2,025 | +1,590 tokens |

The overhead was traced to standalone `load_skill` turns (34), `todo` turns (13), 4 unnecessary confirmation nudges, and tool-spec size. v1.1 addressed each ([ADR-009](adr/ADR-009-measured-harness-overhead.md)); the before/after measurement is in [evaluation/RESULTS.md](evaluation/RESULTS.md).

## 4. Cost model

For a hosted endpoint priced at `p_in` / `p_out` per million tokens:

```text
cost ≈ Σ_turns [(prefix + history_t) · p_in · (1 − cache_hit_t · discount) + output_t · p_out]
```

With the v1 GLM-5.3 figures (1.18 M input, 88 k output over 28 tasks) the median task used ~45 k tokens. NIM's hosted trial endpoints used here are free; plug in your provider's prices to budget. `RunResult.usage` exposes `input_tokens`, `output_tokens` and `cached_tokens` per run (including sub-agents).

## 5. Throughput & concurrency

- Sessions are independent; one `Runtime` can run many concurrently (the eval runner uses a thread pool).
- On a shared endpoint, concurrency raises per-call latency: 4 workers → p90 35 s vs single-session calls of 1–6 s in probes. Rate limits (429) are absorbed by jittered retries; 2 % of v1 calls needed a retry.
- Sub-agents multiply concurrency (up to `max_parallel_subagents` per parent).

## 6. Performance guidelines

1. Prefer a strong primary model: the stronger model in our runs used **~43 % fewer tokens** for a higher pass rate (GLM-5.3 1.27 M vs Nemotron-3-Super 2.24 M on the same 28 tasks).
2. Keep `max_tool_output_chars` modest; rely on spill files and grep for the rest.
3. Do not raise `keep_recent_turns`/`keep_recent_tool_results` without evidence; they multiply every call.
4. Put repeated procedures in workflows — they spend zero tokens on control flow.
5. Use `verify_command` rather than the judge where possible (free, exact).
