# 12 — Observability

> Status: **Implemented** · Code: `observability/`, `events.py`, `cli.py (trace, sessions)`

Three complementary views of every run, all local files, all derived from the same execution:

| View | File / output | Granularity | Best for |
|---|---|---|---|
| **Event log** | `events.jsonl` | every fact (full payloads) | audit, resume, replay, deep debugging |
| **Trace** | `trace.jsonl` (OTLP-shaped spans) | timing of agents, model calls, tool calls | latency analysis, cost attribution, export to APM |
| **Console** | stderr, live | one line per decision | watching a run |

## 1. Spans (OpenTelemetry GenAI semantic conventions)

| Span name | `gen_ai.operation.name` | Parent | Attributes |
|---|---|---|---|
| `invoke_agent <agent_id>` | `invoke_agent` | root, or the parent agent's span for sub-agents | `gen_ai.agent.name=polymath`, `gen_ai.agent.id`, `gen_ai.conversation.id` (= session id) |
| `chat <model>` | `chat` | agent span | `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.response.finish_reasons`, `polymath.estimated_input_tokens`, `polymath.attempts`, `polymath.protocol`, `error.type` on failure |
| `execute_tool <tool>` | `execute_tool` | agent span | `gen_ai.tool.name`, `gen_ai.tool.call.id`, `gen_ai.tool.type=function`, `polymath.tool.is_error`, `polymath.tool.output_chars` |

Format: one JSON object per line with `traceId` (32 hex), `spanId` (16 hex), `parentSpanId`, `name`, `startTimeUnixNano`, `endTimeUnixNano`, `attributes`, `status{code,message}` — the OTLP JSON shape, so a ~20-line converter can ship it to any OTel collector. The GenAI conventions are still *experimental* upstream (moved to a dedicated repo in v1.42.0, June 2026); the names used here are the ones major vendors already ingest.

```text
$ polymath trace 20260924T235501Z_48996f5c
    0.00s invoke_agent main [25.62s]
    0.00s   chat z-ai/glm-5.3 [6.49s] in=3,533 out=363
    6.49s   execute_tool load_skill [0.00s]
    6.50s   chat z-ai/glm-5.3 [3.97s] in=3,899 out=335
   10.47s   execute_tool write_file [0.00s]
   ...
```

## 2. Console renderer

Subscribed to the event log; renders per event type with indentation per agent depth and ANSI colours on a TTY:

```text
━━ task Write a Python script primes.py that prints the number of primes below 1,000,000 …
  I'll load the relevant skill, then write and run the script.
  ▶ load_skill name='python-engineering'
    ✓ # Skill: python-engineering
  ▶ bash cd … && python3 primes.py
    ✓ Traceback (most recent call last):  [exit code: 1 | 0.02s | cwd: …]
  numpy isn't installed — I'll switch to a pure standard-library sieve.
  ...
━━ completed (finished) · 7 turns · 7 tool calls · 33,028 tokens · 25.6s
```
Verbosity: `-q` (0) silent, default (1) steps, `-v` (2) adds reasoning excerpts, routing profile and harness/verifier messages.

## 3. Metrics you can compute from a session (and the eval runner does)

| Metric | Source |
|---|---|
| Turns, tool calls, tokens in/out, requests | `task.completed.result` (includes sub-agents) |
| Per-call latency, attempts (retries), protocol | `model.response.response.{latency_s, attempts, protocol}` |
| Estimator accuracy | `model.response.est_input_tokens` vs `usage.input_tokens` |
| Tool mix, error rate, durations | `tool.result` |
| Context pressure | `context.cleared` / `context.compacted` counts and `before_tokens` |
| Harness interventions | `message.user` with `source ∈ {harness, verifier}`; `harness.note` kinds |
| Verification outcomes | `verification.result` |
| Fail-over events | `models_used` > 1 in the run result |

## 4. Debugging playbook

| Symptom | Look at |
|---|---|
| Run stopped unexpectedly | `polymath trace <id> --events \| tail` → `task.completed.stop_reason`, `model.error`, `harness.note` |
| Slow run | `polymath trace <id>`: long `chat` spans ⇒ provider latency/queueing (`polymath.attempts` > 1 ⇒ retries); long `execute_tool` ⇒ the command |
| Agent went in circles | `harness.note{kind: loop_detected}` and the repeated calls before it |
| Wrong answer | the `tool.result`s the answer was based on; replay with `polymath replay <id>` after a fix |
| Context blow-up | `context.compacted.before_tokens`, spill files in `outputs/` |
| Crash | resume: `polymath resume <id>`; pending calls are re-executed |
