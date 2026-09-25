# Evaluation Results

> Live runs on NVIDIA NIM (`integrate.api.nvidia.com`), 24–25 September 2026. Every number below is produced by `evals/runner.py` and `evals/compare.py` from the `results.json` files in [`evals/results/`](../../evals/results). Nothing is hand-tuned; failures are reported and classified.

<!-- RESULTS:SUMMARY -->

## 1. Setup

| Item | Value |
|---|---|
| Endpoint | NVIDIA NIM hosted API (shared, OpenAI-compatible) |
| Models | `z-ai/glm-5.3` (primary), `nvidia/nemotron-3-super-120b-a12b`; utility `nvidia/nemotron-3.5-lightning-30b-a3b` |
| Suites | **core** — 28 tasks, 11 categories · **hard** — 6 tasks ([13 §2.2](../13-evaluation-and-testing.md#22-task-inventory)) |
| Verifiers | hidden, deterministic; validated both ways by `python -m evals.reference`: every reference solution accepted and every untouched workspace rejected (34/34 each for the v1 runs; 36/36 each since the retention tasks). The untouched-workspace direction was a one-off check until it was found missing from the code during the v2 audit; it is now part of the command, `eval`-cheat rejected) |
| Isolation | fresh workspace per task outside the repo; model fail-over **disabled** in comparative runs (`--no-fallback`) so each result belongs to one model |
| Harness modes | **full** (14 tools, skills, hints, memory, nudges, verification, loop detection) · **minimal** (bash, read/write/edit, finish; no skills/hints/memory/nudges/verification) |
| Harness versions | **v1.0** initial · **v1.1** overhead reductions (ADR-009) · **v1.2** streaming gateway (ADR-010) + resumable failures + wrap-up hardening |
| Concurrency | 3–4 sessions per run; several runs in parallel on one account (latency figures reflect a loaded shared endpoint) |

<!-- RESULTS:CORE -->

<!-- RESULTS:HARD -->

<!-- RESULTS:CONTEXT -->

## Live durability: resuming a run killed by a provider outage

`data-sqlite-analytics` on Nemotron-3-Super (v1 run) died after 4 turns: **seven consecutive HTTP 500s** from the endpoint exhausted the retry budget (fail-over was disabled for the measurement). This surfaced a real defect — `resume` returned the stored failure instead of continuing — which was fixed (v1.2: `model_error`/`no_progress`/`crash` failures are resumable; wall budgets count active time only). The same session was then resumed against the live model:

```text
· resumed: after model_error; re-executing 0 pending tool call(s)
▶ write_file compute.py (1,793 chars)
▶ bash python3 compute.py      ✓ {'top_customers': [...], 'customers_without_orders': 5, 'avg_order_value_2025': 1098.15}
▶ write_file verify.py (2,557 chars)
▶ bash python3 verify.py       ✓ independent cross-check
▶ read_file answer.json
▶ finish …
━━ completed (finished) · 10 turns · 62,749 tokens
hidden verifier: PASS (3 checks)
```
The agent continued with its full prior context (schema already explored) — no work was repeated.

## Router accuracy experiment

Heuristic keyword routing vs an LLM router on the 28 core instructions (acceptable-category sets per task):

| Router | Correct | Latency (28 calls, 6 parallel) | Notes |
|---|---|---|---|
| Heuristic v1 (substring) | ~17/28 | 0 s | routed `merge_intervals(…)` to **git**; the model then loaded the git skill (wasted turn) |
| Heuristic v1.1 (word-boundary, git needs evidence) | 21/28 | 0 s | still confuses "debug mode"→debugging, "JSON"→data |
| LLM `nemotron-3.5-lightning` | 0/28 answered | 83.6 s | reasoning consumed the 300-token budget → all fell back (bug; budget raised to 2,000) |
| LLM `glm-5.3-flash` | 23/28 | 312 s | too slow for an intake step |

**Decision:** keep the free heuristic, but only emit skill suggestions on an unambiguous keyword winner, and inline (rather than hint) the top skill when confident. A wrong hint is worse than none.

## Defects found by evaluation (and fixed)

| # | Found by | Defect | Fix |
|---|---|---|---|
| 1 | unit test (delegation) | parallel sub-agents shared one shell-script directory → commands overwrote each other | per-agent scratch dir + nonce in script names |
| 2 | unit test (protocol) | text protocol dropped tool instructions when a request had no system message | inject a system message |
| 3 | unit test (notes) | `notes` failed if the session dir did not exist | mkdir parents |
| 4 | `-W error::ResourceWarning` | pipes leaked when replacing a dead shell | dispose dead process pipes |
| 5 | reference solutions | verifier regex banned `re.compile` in the no-`eval` check | builtin-only lookbehind |
| 6 | reference solutions | wrong hand-written reference schedule | brute-force solver proves solvability (50/40,320 valid) |
| 7 | live trace | router matched `merge` inside `merge_intervals` → git skill | word-boundary regexes + regression test |
| 8 | live ablation | full harness used 2.1× the tokens of minimal for the same pass rate | ADR-009 overhead reductions |
| 9 | live run | nudging already-final prose answers cost a turn each (4/4 cases were final) | `looks_final` heuristic |
| 10 | live router test | LLM router starved reasoning models (300 tokens) | 2,000-token budget |
| 11 | live resume | failed (`model_error`) sessions could not be resumed | resumable stop reasons + active-time wall budget |
| 12 | live hard tier | non-streaming requests timed out on long generations; retries stacked server-side work | SSE streaming with idle timeouts (ADR-010) |
| 13 | live Nemotron run | model ignored the finish-only toolset in the wrap-up turn → no answer | forced `tool_choice`, then synthesised partial answer |
| 14 | unit test (streaming) | SSE body starting with a keep-alive comment was parsed as JSON | detect SSE by any `data:` line |
| 15 | docs quality gate (`scripts/check_diagrams.py`) | 2 of 15 Mermaid diagrams would not render (`;` inside sequence messages) | rephrased; all 15 parse with Mermaid 11 |

## Threats to validity

- **Suite size.** 34 tasks is enough to find defects and large effects, not to separate models within a few points. Pass rates of 100 % mean the suite no longer discriminates at the top for that model.
- **Single samples.** Most cells are one run; repeats were limited by endpoint throughput. Treat per-task differences as anecdotal, aggregate token effects as indicative.
- **Shared endpoint.** Latency and some failures depend on NIM load (we observed p90 35 s, max 213 s per call, bursts of HTTP 500). Wall-time figures are not model properties.
- **Author-written tasks.** Tasks and verifiers were written by the same team as the harness; the reference-solution check guards verifier correctness but not task selection bias.
- **Harness version drift.** v1.0 → v1.2 changed several things at once; the v1.2 core run measures their combined effect, not each change separately.
