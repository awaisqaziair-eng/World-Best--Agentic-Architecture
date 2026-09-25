# 14 — Configuration & Operations

> Status: **Implemented** · Code: `polymath/config.py`, `polymath/cli.py`

## 1. Installation

```bash
git clone <this repo> && cd World-Best--Agentic-Architecture
python3 --version            # ≥ 3.11, nothing else required (standard library only)
pip install -e .             # optional: installs the `polymath` command
export NVIDIA_NIM_API_KEY=nvapi-...   # never commit keys; the repo .gitignore excludes *.env
```

## 2. Configuration precedence

`CLI / API overrides` > `environment variables` > `polymath.toml` (in `./`, then `~/.polymath/`) > built-in defaults. Unknown keys in `polymath.toml` are an error (typos never silently fall back to defaults). Sections are allowed for readability and are flattened.

```toml
# polymath.toml
[models]
model = "z-ai/glm-5.3"
fallback_models = ["nvidia/nemotron-3-super-120b-a12b"]
utility_model = "nvidia/nemotron-3.5-lightning-30b-a3b"

[kernel]
max_turns = 60
verify = "auto"

[model_params."my/custom-model"]
temperature = 0.2
context_window = 64000
tool_protocol = "text"          # force the text tool protocol for this model
extra_body = { top_p = 0.9 }    # merged into every request body
```

Environment variables: `NVIDIA_NIM_API_KEY` (name set by `api_key_env`), `POLYMATH_MODEL`, `POLYMATH_BASE_URL`, `POLYMATH_UTILITY_MODEL`, `POLYMATH_FALLBACK_MODELS` (comma-separated), `POLYMATH_TOOL_PROTOCOL`, `POLYMATH_HOME`, `POLYMATH_SESSIONS_DIR`, `POLYMATH_VERBOSITY`, `POLYMATH_MAX_TURNS`, `POLYMATH_CONTEXT_WINDOW`.

## 3. Configuration reference (generated from `Config`)

| Key | Default | Meaning |
|---|---|---|
| **Gateway** | | |
| `base_url` | `https://integrate.api.nvidia.com/v1` | any OpenAI-compatible endpoint |
| `api_key_env` | `NVIDIA_NIM_API_KEY` | name of the env var holding the key |
| `model` | `z-ai/glm-5.3` | primary model |
| `fallback_models` | `[nemotron-3-super-120b-a12b, nemotron-3-ultra-550b-a55b]` | fail-over chain (empty disables) |
| `utility_model` | `nvidia/nemotron-3.5-lightning-30b-a3b` | compaction, judge, LLM router |
| `tool_protocol` | `auto` | `auto` / `native` / `text` |
| `temperature` | `None` | `None` → per-model default from `model_params` |
| `max_output_tokens` | `16384` | reserved per response |
| `request_timeout_s` | `240` | per HTTP request |
| `max_retries` | `6` | transient-error retries per request |
| `retry_base_s` / `retry_cap_s` | `1.5` / `45` | jittered exponential backoff |
| `breaker_failures` / `breaker_cooldown_s` | `3` / `90` | circuit breaker per model |
| `seed` | `None` | passed to the provider when set |
| `send_reasoning_back` | `False` | re-send `reasoning_content` in history |
| `model_params` | per-model table | temperature, context_window, tool_protocol, extra_body |
| **Context** | | |
| `context_window` | `None` | override the model window |
| `clear_at` | `0.45` | clearing threshold (fraction of usable window) |
| `compact_at` | `0.70` | compaction threshold |
| `keep_recent_tool_results` | `8` | never cleared |
| `keep_recent_turns` | `6` | kept verbatim on compaction |
| `clear_min_chars` | `1200` | shorter outputs are never stubbed |
| **Tools** | | |
| `shell` | `/bin/bash` | |
| `shell_timeout_s` / `shell_max_timeout_s` | `180` / `1800` | default and cap per command |
| `max_tool_output_chars` | `24000` | model-facing output budget |
| `tool_parallelism` | `8` | concurrent parallel-safe calls |
| `enable_web` / `enable_memory` / `enable_delegation` | `True` | toolset switches |
| `max_delegation_depth` | `1` | sub-agents cannot delegate further |
| `max_parallel_subagents` | `4` | |
| `hermetic_env_strip` | `*_API_KEY, *_TOKEN, *_SECRET` | env vars not inherited by the agent shell |
| `skill_dirs` | `[]` | extra skill directories |
| **Kernel** | | |
| `max_turns` / `max_tokens` / `max_wall_s` / `max_tool_calls` | `80` / `3,000,000` / `3600` / `400` | default task budget |
| `verify` | `auto` | `auto` / `off` / `always` |
| `max_verify_rounds` | `2` | |
| `router` | `heuristic` | `heuristic` / `llm` / `off` |
| `loop_window` / `loop_repeat_threshold` | `10` / `3` | identical-call loop detector |
| `text_answer_grace` | `2` | prose-only replies before prose is accepted as final |
| **Storage & observability** | | |
| `home` | `~/.polymath` | |
| `sessions_dir` / `memory_path` | under `home` | |
| `fsync_events` | `False` | fsync every event (power-loss durability) |
| `verbosity` | `1` | 0 quiet · 1 steps · 2 steps + reasoning |

## 4. CLI reference

| Command | Purpose | Example |
|---|---|---|
| `polymath run TASK [-w DIR]` | run a task to completion | `polymath run "fix the failing tests" -w ./repo --verify "python3 -m unittest"` |
| options | `--criteria C` (repeatable) · `--verify-mode auto/off/always` · `--max-turns N` · `--max-time S` · `--mode full/minimal` · `-i` (agent may ask you) · `--json` · `--model M` · `--utility-model M` · `--no-fallback` · `--protocol auto/native/text` · `-q/-v` | |
| `polymath chat [-w DIR]` | interactive multi-turn session | |
| `polymath resume SESSION` | continue an interrupted/failed session | `polymath resume 20260925T001227Z_ab12cd34` |
| `polymath replay SESSION [-w DIR] [--strict]` | re-run with recorded model outputs | |
| `polymath sessions` | list sessions with state | |
| `polymath trace SESSION [--events]` | span tree or raw events | |
| `polymath models` | list endpoint models (`*` primary, `f` fallback, `u` utility) | |
| `polymath workflow NAME --input k=v …` | run a deterministic workflow | `polymath workflow fix-until-green -w ./repo --input cmd="python3 -m unittest"` |
| `python -m evals.runner …` | evaluation suite | see [13](13-evaluation-and-testing.md) |

Exit codes: `0` completed, `1` failed, `130` interrupted (resume later).

## 5. Python API

```python
from polymath import Runtime, load_config, Budget

rt = Runtime(load_config(model="z-ai/glm-5.3"))
res = rt.run("Summarise report.txt into summary.md (≤150 words)", "./work",
             acceptance_criteria=["summary.md exists and is ≤150 words"],
             budget=Budget(max_turns=30))
print(res.state, res.stop_reason, res.answer, res.usage.total)
```

## 6. Runbook

| Situation | Action |
|---|---|
| Provider outage mid-run | Run ends `failed/model_error`; after recovery: `polymath resume <session>`. With fallbacks configured the run usually fails over automatically. |
| Process killed / machine rebooted | `polymath resume <session>`; the torn last event line is repaired; interrupted tool calls re-run (at-least-once). |
| 429s / slow responses | Normal on shared endpoints; retries with jittered backoff absorb them. Reduce concurrency (fewer parallel sessions / sub-agents): concurrent long generations share the account's token throughput (measured: ~60 → ~10 tokens/s per stream at ~9 concurrent GLM-5.3 streams). |
| Model lacks native tool calling | Automatic downgrade to text protocol, or force with `--protocol text` / `model_params.<m>.tool_protocol = "text"`. |
| Context-length errors | Automatic compaction + retry; lower `context_window` if a model's real window is smaller than configured. |
| Runaway command | Per-command timeout escalation; the agent sees a `[TIMEOUT …]` footer. |
| Disk usage | Sessions are plain directories; delete old ones (`rm -rf ~/.polymath/sessions/<id>`). Spill files live in `outputs/`. |
| Reproduce a bad run | `polymath replay <session> -w /tmp/fresh` (tools re-run against recorded model outputs). |

## 7. Testing

```bash
python3 -m unittest discover -s tests -t .          # 122 offline tests, ~12 s, no network
python3 -m evals.reference                           # verifier self-test (34 tasks, both directions)
python3 -m evals.runner --tasks core --out /tmp/run  # live evaluation (needs an API key)
```
