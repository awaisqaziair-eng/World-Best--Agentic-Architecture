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

## 5. Results

*Filled from `results.json` when repeat 1 completes for every stack.*

## 6. Threats to validity

- **One model.** Nemotron-3-Super only. GLM-5.3 doesn't serve the Responses API on NIM (R1), so the OpenAI Agents SDK couldn't be compared on it.
- **One repeat.** 28 trials per stack. A difference of one or two tasks is within run-to-run noise: we observed the same stack pass and fail `ops-config-audit` on consecutive smoke runs.
- **Author-written suite.** The tasks were written for v1, and v1's harness may fit them better. The prebuilt stacks meeting v1 on v1's own suite is the stronger direction of that bias.
- **Shared endpoint load.** Throughput and throttling varied over the hours of the run. The governor made them shared, not constant.
