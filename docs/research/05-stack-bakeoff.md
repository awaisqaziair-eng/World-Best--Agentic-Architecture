# R5 — Stack bake-off: four agent stacks, same tasks, same model, same hidden verifiers

> Status: **Running** (results below are filled from `results.json` files as they complete) · Harness: `evals/runner.py --stack …`, `evals/backends.py` · Model: `nvidia/nemotron-3-super-120b-a12b` on NVIDIA NIM, temperature 0.3, fail-over off

## 1. Question

Which prebuilt stack is the best foundation for v2? Then, separately: do Polymath's swaps make that foundation better on real tasks? R1 measured capability breadth by reading source. This note measures **outcomes**.

## 2. Stacks and exact configurations

| Stack | What runs | Resilience configuration (each project's own mechanism) |
|---|---|---|
| `polymath-v1` | the v1.2 harness, full mode | its own gateway retries (streaming, idle timeouts) |
| `deepagents` | `create_deep_agent(model=ChatOpenAI(…), backend=LocalShellBackend(root_dir=ws, virtual_mode=False, timeout=180))`, defaults otherwise | `ChatOpenAI(max_retries=6, streaming=True, stream_usage=True)` + `ModelRetryMiddleware(max_retries=4, on_failure="error")` |
| `pydanticai-coder` | `Agent(model, capabilities=[Coder(workspace=ws)])`, defaults otherwise | `AsyncHTTPX2TenacityTransport` (429/5xx, `wait_retry_after`, 7 attempts) + `RequestRetryModel` equaliser (retries request and stream-open on in-body errors) |
| `openai-agents` | `SandboxAgent` (default capabilities: Filesystem, Shell, Compaction) on `UnixLocalSandboxClient`, `Manifest(root=ws)`, Responses API | `AsyncOpenAI(max_retries=6)` + `ModelRetrySettings(max_retries=4, policy=any(provider_suggested, network_error, retry_after, http_status[429,5xx]))` |

**Fairness rules.**
1. Each stack uses its **default harness** plus its **own** recommended resilience layer, never Polymath code.
2. Same model, endpoint, temperature, turn/request budget (the task's `max_turns`), workspace setup and hidden verifier.
3. Token usage comes from each SDK's own accounting.
4. **All stacks sit behind one egress governor** (R4), so account-level throttling is shared identically and doesn't decide outcomes.

Two exceptions to rule 1, both applied to make the comparison *about the harness* and both disclosed:
- `on_failure="error"` for LangChain's `ModelRetryMiddleware`. Its default turns exhausted retries into fake completions (R1 D3), which would misreport outcomes.
- The `RequestRetryModel` equaliser for Pydantic AI. Its shipped transports can't see errors inside a 200 (D5), and LangChain's middleware already retries those.

## 3. Protocol

- Core suite: 28 tasks, 11 categories, hidden deterministic verifiers ([13](../13-evaluation-and-testing.md)).
- 2 workers per stack, all four stacks concurrently. Repeat 1 is complete for every stack before repeat 2 starts.
- **Budget decision.** The account sustained about 0.4–0.5 req/s, which made two repeats for four stacks a ~9-hour run. Each stack is stopped after repeat 1 (28 trials), to free the budget for the v2 and retention experiments. With one repeat, per-task differences are anecdotal; only large aggregate differences are meaningful (§6).

## 4. Infrastructure incidents during the run (all disclosed)

| Time | Incident | Effect | Handling |
|---|---|---|---|
| start | First launch without the governor (3 stacks) | 429 storms; deepagents "completed" runs that had actually failed (D3) | Aborted; results discarded; governor built (R4); LangChain `on_failure="error"` |
| +3 h | Plain AIMD drove the governor to 4 req/min against provider-side throttling (R4 §4) | Throughput collapsed ~8×; no failures (0 give-ups) | Governor replaced by the loss-tolerant version at 02:16:18 (container time). Clients retried the ~1 s outage; trials overlapping the swap are audited in §5 |
| 02:20–03:10 | NIM degradation on `nemotron-3-super`: 50–70 % of attempts throttled even at 0.1 req/s; 31 governor give-ups | v1's 900 s wall budget counted queue time, so two v1 trials failed on time alone (defect 19); prebuilt stacks have no wall budget and simply waited | **Runners paused (SIGSTOP) at 03:10:31**; direct probes every 10 min (6/10, 5/10, 7/10, 3/10, 7/10, 8/10 with none of our traffic). **Resumed (SIGCONT) at 04:10:02** after two consecutive probes ≥ 7/10. Trials in flight across the pause are flagged in §5 |
| 04:10–06:29 | `nemotron-3-super` degraded again after resuming: 45 of 98 governor requests given up in one hour | v1 accumulated **four** time-budget failures (one at 3,647 s, counting the pause); the prebuilt stacks, having no wall budget, did not | **Super bake-off stopped at 06:29** (partial: v1 24, deepagents 22, Coder 18, Agents SDK 16 trials, kept as a secondary dataset). **The full comparison moved to `nemotron-3-ultra`**: deepagents, the Agents SDK and v1 added next to the Coder/v2 runs; v1 runs with `--no-wall-budget`, so every stack faces the same turn budget and none a wall clock |
| 03:45 | The same probe showed other models healthy: `nemotron-3-ultra` 6/6, `kimi-k3` 6/6, `gpt-oss-20b` 6/6; `glm-5.3` 0/6 | Capacity is per model (R4 §4.1) | Model-independent experiments moved to **nemotron-3-ultra** on a separate governor (port 8788): the paired `pydanticai-coder` vs `polymath-v2` core comparison and the retention arms. The four-stack table stays on `nemotron-3-super` and resumes when it recovers |

## 5. Results

### 5.1 Paired comparison on `nemotron-3-ultra`: prebuilt Coder vs Polymath v2 (core suite, 28 tasks × 2 repeats)

Generated by `python research/analyze_bakeoff.py` (data: `evals/results/ultra-core-*`).

| Run | Pass | Median turns | Median tokens/task | Total tokens |
|---|---|---|---|---|
| Coder, repeat 1 | 25/28 | 6 | 21.5 k | 1,157 k |
| Coder, repeat 2 | 25/28 | 6 | 22.0 k | 1,433 k |
| v2, repeat 1 | 26/28 | 6 | 22.8 k | 909 k |
| v2, repeat 2 | 24/28 | 6 | 23.6 k | 1,149 k |

Paired over 56 task-repeat pairs: **both pass 48 · only Coder 2 · only v2 2 · both fail 4.** Exact sign test on the discordant pairs: **p = 1.00**.

- **No detectable difference in pass rate on ordinary tasks (50/56 each).** This is the expected result for a suite that rarely stresses context (v2 evicted nothing on most tasks). The swaps do no harm, and the suite can't show where they help.
- **Discordant pairs.** v2 won `git-feature-flow` in **both** repeats. Coder's shell leaves an untracked `__pycache__/`; Polymath's terminal runs with `PYTHONDONTWRITEBYTECODE=1`, a systematic hygiene difference. Coder won `ops-config-audit` and `ops-organize-files` once each. The v2 failures were model slips with no eviction involved (e.g. an extension-less file filed under `docs/`), i.e. run-to-run variance.
- **Tokens: +11 % on the median pair** (per-pair ratio v2/Coder 1.11, IQR 0.97–1.27), **but −21 % in total** (2,058 k vs 2,590 k), because a few Coder runs were very expensive. Both statements are true, and which one matters depends on whether you pay for the typical task or the tail.
- Where v2's swaps *do* change outcomes is measured separately: context retention under pressure (R6: the prebuilt clearing 2/4, v2 4/4, with the failure mechanism in the transcripts), shell robustness (R3: 14/14 vs 10/14) and rate-limit resilience (R4).

### 5.2 Four-stack table on `nemotron-3-super`

*Filled when repeat 1 completes for every stack.*

## 6. Threats to validity

- **One model.** Nemotron-3-Super only. GLM-5.3 doesn't serve the Responses API on NIM (R1), so the OpenAI Agents SDK couldn't be compared on it.
- **One repeat.** 28 trials per stack. A difference of one or two tasks is within run-to-run noise: we observed the same stack pass and fail `ops-config-audit` on consecutive smoke runs.
- **Author-written suite.** The tasks were written for v1, and v1's harness may fit them better. The prebuilt stacks meeting v1 on v1's own suite is the stronger direction of that bias.
- **Shared endpoint load.** Throughput and throttling varied over the hours of the run. The governor made them shared, not constant.
