# R3 — Terminal bake-off: six shells, fourteen adversarial scenarios

> Status: **Measured** · Date: 2026-09-25 · Harness: [`research/terminal_bakeoff.py`](../../research/terminal_bakeoff.py) · Raw data: [`research/results/terminal_bakeoff.json`](../../research/results/terminal_bakeoff.json)
>
> Reproduce: `.venv/bin/python research/terminal_bakeoff.py` (≈6 min, no model or network needed).

## 1. Question

The v2 decision is "use prebuilt SDKs and enhance them where they fall short" (R1). Every candidate SDK ships a shell tool, so the question is concrete: **is Polymath's persistent terminal still worth carrying into v2, or does a prebuilt shell do the job?** It only earns its place if it is measurably better on the failures that actually derail agents.

## 2. What was compared

| Adapter | Implementation (exact layer driven) | Version |
|---|---|---|
| `polymath` | `polymath.tools.terminal.PersistentShell.run` | this repo |
| `langchain` | `langchain.agents.middleware.shell_tool.ShellSession.execute`, the session behind `ShellToolMiddleware`, with `HostExecutionPolicy` | langchain 1.4.2 |
| `deepagents` | `deepagents.backends.LocalShellBackend.execute`, the backend of deepagents' `execute` tool | deepagents 0.7.19 |
| `pydanticai-shell` | `pydantic_ai_harness.shell.ShellToolset.run_command`, the default tool of the `Shell` capability, with `persist_cwd=True`, plus the `call_tool` tail cap that sits above it | pydantic-ai-harness 0.34.0 |
| `pydanticai-coder` | the `shell` tool that the `Coder` capability registers, **called through a real `Agent`** driven by a scripted `FunctionModel`, so dispatch, output limits and formatting are exactly what a model sees | pydantic-ai 2.49.0 |
| `openai-agents` | `agents.sandbox` `exec_command` (the `SandboxAgent` Shell capability) on a `UnixLocalSandboxClient` session | openai-agents 0.22.3 |

**Fidelity rule.** Each adapter must drive the layer that produces what the *model* sees. Where a transformation sits above the called function (for example a truncation seam), the adapter applies it. §6 lists the three places where an earlier version of this benchmark broke that rule, and the corrections.

**Isolation.** Each (shell, scenario) pair runs in a fresh subprocess and a fresh temporary workspace, with a 75 s hard limit, so a hang is recorded as a failure rather than stalling the run. Afterwards, any process whose cwd is inside the workspace is killed and counted (`reaped`). Some designs deliberately leave timed-out commands running, and without this a busy loop would outlive the benchmark.

## 3. Scenarios and why each one matters

Each scenario reproduces a failure seen in real agent transcripts or in our own eval runs. Steps without an explicit timeout get 20 s.

| # | Scenario | Steps | Pass condition | Failure it models |
|---|---|---|---|---|
| 1 | `cd_persists` | `mkdir -p a/b && cd a/b` → `pwd` | some output line ends in `/a/b` | The model `cd`s once and assumes it stuck. Stateless shells silently run every later command in the wrong directory. |
| 2 | `env_persists` | `export FOO=bar` → `echo $FOO` | `bar` printed | Activated virtualenvs and exported config are lost. |
| 3 | `function_persists` | `f(){ echo fn-$1; }` → `f x` | `fn-x` printed | Helpers defined once can't be reused. |
| 4 | `heredoc` | multi-line `python3 - <<'PY' … PY` | `45` printed | Multi-line scripts are the most common "code mode" call. |
| 5 | `unbalanced_quote` | `echo 'unclosed` → `echo alive` | step 2 works and step 1 took < 10 s | A model typo that leaves the shell waiting for a closing quote. |
| 6 | `stdin_read` | `read x; …` → `echo next` | step 1 < 5 s | A tool unexpectedly prompts (`apt`, `git` credential, `read`). |
| 7 | `exit_code` | `false` | exit code 1 reported | The model needs the status to know a test failed. |
| 8 | `timeout_returns` | `sleep 30` with a 2 s timeout → `echo after` | step 1 < 9 s and the shell still works | Hung commands must return control on time. |
| 9 | `timeout_grandchild_pipe` | `sh -c 'sleep 25'; echo done` with 2 s → `echo after` | same | A grandchild holding the output pipe open defeats naive "wait for EOF" timeouts. |
| 10 | `background_survives_timeout` | start `nohup sleep 60 &` → a timed-out `sleep 30` → `kill -0 <bg pid>` | background still alive | A server started earlier must not be killed because a later command timed out. |
| 11 | `exit_recovers` | `exit 7` → `echo alive` | step 2 works | The model runs `exit` (or a script calls it) and kills its own shell. |
| 12 | `huge_output_bounded_tail_kept` | `yes abcdefghij \| head -c 20000000; echo END` | returned text < 10 MB **and** `END` present | A 20 MB log. The context must be protected, but the **end** (where errors and summaries live) must survive. The filler never contains `END`, so its presence proves the tail was kept. |
| 13 | `progress_bar_collapsed` | `printf '10%\r50%\r100%\ndone\n'` | `10%` absent, `100%` present | Progress bars (`pip`, `curl`, `tqdm`) flood the context with carriage-return frames. |
| 14 | `busy_loop_recovers` | `while true; do :; done` with 2 s → `echo ok` | step 2 works, total < 30 s | A loop inside the shell process itself, with no child process to kill. |

## 4. Results

| Scenario | polymath | langchain | deepagents | pydanticai-shell | pydanticai-coder | openai-agents |
|---|---|---|---|---|---|---|
| cd_persists | ✅ | ✅ | ❌ | ✅ | ❌ | ❌ |
| env_persists | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| function_persists | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| heredoc | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| unbalanced_quote | ✅ 0 s | ❌ 30 s | ✅ 0 s | ✅ 0 s | ✅ 0 s | ✅ 0 s |
| stdin_read | ✅ 0 s | ❌ 20 s | ✅ 0 s | ❌ 20 s | ✅ 0 s | ❌ 20 s |
| exit_code | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| timeout_returns | ✅ 2 s | ❌ 12 s | ✅ 2 s ⚠ | ✅ 2 s | ✅ 2 s ⚠ | ✅ 2 s |
| timeout_grandchild_pipe | ✅ 2 s | ❌ 12 s | ✅ 2 s | ✅ 2 s | ✅ 2 s | ✅ 2 s |
| background_survives_timeout | ✅ | ❌ | ✅ | ✅ | ✅ | ❌ † |
| exit_recovers | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| huge_output_bounded_tail_kept | ✅ | ❌ | ❌ | ✅ | ✅ | ❌ |
| progress_bar_collapsed | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| busy_loop_recovers | ✅ 3.5 s | ✅ 12 s | ✅ 2 s | ✅ 2 s | ✅ 2 s ⚠ | ✅ 2 s |
| **Total** | **14/14** | **7/14** | **9/14** | **10/14** | **10/14** | **7/14** |

⚠ = passed, but left processes running afterwards (the reaper found them; §5).
† = a real upstream bug, not just a design difference (§5.6).

These are deterministic measurements, not samples. Re-running reproduces the same pass/fail cells; timings vary by about ±0.1 s.

## 5. What the failures mean, harness by harness

### 5.1 LangChain `ShellSession` (7/14)
It is a real persistent bash, so state persists (1–3). Its timeout path is the problem. On timeout, `ShellSession` writes `exit` to a shell that is busy and never reads it, waits `termination_timeout` (default **10 s**), kills the process and starts a new session (`restart()`, `shell_tool.py:186–215, 300–305`). The consequences:

- timeouts take 12 s instead of 2 s (8, 9);
- a background server started earlier dies with the session (10);
- a waiting shell (5, 6) recovers only through that timeout-and-restart path (30 s and 20 s);
- the result is `output=""`, so **everything the command printed before it hung is discarded**, exactly the diagnostics a model needs to understand the hang.

Output is capped at `max_output_lines=100` by dropping every line **after** the cap, so the tail of a long log is lost (12). No `\r` handling (13).

### 5.2 deepagents `LocalShellBackend` (9/14)
Each call is a fresh `sh -c` via `subprocess.run`: **no state persists** (1–3), which is by design. stdin is not attached, so prompts fail fast (6 ✅). On timeout only the `sh` process is killed, and the timed-out `sleep 30` survives as an **orphan** (⚠ in 8; the reaper killed it). Output over 100 000 bytes is cut **head-first** with `... Output truncated at 100000 bytes.`, so the tail is lost (12). The filesystem middleware's large-result offload runs afterwards, on text that is already truncated, so it cannot bring the tail back.

### 5.3 Pydantic AI `Shell` default tool, `run_command` (10/14)
One process per call, with opt-in cwd tracking (`persist_cwd=True`) so `cd` survives (1). Environment and functions don't (2, 3). stdin is a pipe that is never closed, so `read` blocks until the timeout (6). Timeouts kill the process group, which is correct and fast. Output is capped **tail-first** at the dispatch seam, `[... output truncated, showing last N chars]`, which is the right direction (12 ✅). Raw `\r` frames are kept (13).

### 5.4 Pydantic AI `Coder` shell (10/14)
A different design: every command runs as a detached, supervised process group. A foreground call waits up to `timeout` and then **returns handles** (PID, log path, status file) while the command **keeps running**. So timeouts return promptly (8, 9, 14) and the shell never wedges. The price is that nothing is ever stopped automatically: after `timeout_returns` and `busy_loop_recovers` the reaper found 3 and 2 live processes (⚠). A model that doesn't `kill` them leaks CPU for the rest of the session. No state persists between calls (1–3). The tail is kept (12 ✅).

### 5.5 OpenAI Agents SDK `exec_command` (7/14)
Codex-style "unified exec": commands run in a PTY session. After `yield_time_ms` the process keeps running and the model can poll it or type into it with `write_stdin`. That is why `read` "hangs" (6): the session is waiting for the model to type, which is interactivity, not a deadlock. No state persists between calls (1–3). Output is **unbounded by default**: the full 20 MB came back unless the model passes `max_output_tokens` (12). No `\r` handling (13).

### 5.6 Upstream defect: backgrounded commands run in `/` (openai-agents 0.22.3)
Scenario 10 failed for a reason unrelated to timeouts: `bg.pid` was never written into the workspace. Isolated reproduction (the output is `pwd`):

```
'pwd'                 -> '/tmp/tmp9j387tkj'   (workspace)
'true & pwd'          -> '/'                  ← filesystem root
'sleep 0 & wait; pwd' -> '/'
'pwd && true'         -> '/tmp/tmp9j387tkj'
```

Root cause: `agents/sandbox/sandboxes/unix_local.py:905` rewrites the command to `cd <root> && <command>` and starts the process with `cwd="/"`. In shell grammar, `cd X && a & b` parses as `(cd X && a) &` followed by `b`. The `cd` happens inside the backgrounded subshell, and everything after the `&` runs in `/`. A model that starts a dev server with `npm run dev & sleep 2; curl …`, or writes `cmd & echo $! > pid`, is writing into the filesystem root. The same SDK already solves this correctly elsewhere: `_resolve_workdir_command` in `capabilities/tools/shell_tool.py` emits `cd X || exit` + newline, with the comment *"Complete the directory change before any shell list or background job runs."* The fix is to use that form, or simply pass `cwd=workspace_root` to the subprocess.

### 5.7 Polymath `PersistentShell` (14/14)
It is the only design that passes all fourteen, because each mechanism targets one of the failures above:

- a persistent bash, with each command `source`d from a script file (1–4, 11);
- `< /dev/null` on every command (5, 6);
- a nonce completion marker instead of EOF, so a grandchild holding the pipe doesn't matter (9);
- SIGINT → SIGTERM → SIGKILL escalation against **descendants spawned by this command only**, so earlier background jobs survive (8, 10);
- shell replacement only when the shell itself is stuck (14);
- a bounded capture that keeps a frozen head plus a rolling tail and reports the dropped byte count (12);
- carriage-return collapsing (13).

## 6. Benchmark bugs found and fixed (why the first numbers were wrong)

The first published table said pydanticai 8/14. Auditing every red cell at the raw-output level found three bugs **in this benchmark**, all of which penalised a competitor:

| Bug | Effect | Fix |
|---|---|---|
| `cd_persists` checked only the first output line | Pydantic AI prefixes output with `[stdout]`, so a correct `/…/a/b` on line 2 failed | accept any line |
| The Pydantic AI adapter called `run_command` directly | That skips `ShellToolset.call_tool`, which applies the tail cap the model actually sees, so the full 20 MB counted as "unbounded" | apply the same `truncate_tail(…, 50_000)` in the adapter |
| `huge_output` required `END` in the last 200 characters | Coder appends about 250 characters of PID/paths/status **after** the output, so a correctly kept tail failed | `END` anywhere; the filler can't contain it |

A fourth, broader mistake: the first version tested only `run_command`. **`Coder` doesn't use that tool**; it registers the persistent `shell` tool. The `pydanticai-coder` column now tests what Coder ships, through a real Agent.

Also corrected: I had predicted that deepagents would hang on scenario 9 (a grandchild holding the pipe). It doesn't: `subprocess.run` with a timeout returned in 2 s. The prediction was wrong, and the only deepagents problem around timeouts is the orphan in 5.2.

Polymath's own weakness surfaced too. It needed **9 s** to recover from the busy loop, because the escalation waited out its full 3 + 2 + 2 s grace even when there was no child process to signal. It now shortens each step's grace to 0.5 s when there are no targets (`polymath/tools/terminal.py`, regression test `test_unresponsive_shell_is_replaced`, which asserts < 3 s for a 0.5 s timeout). Measured now: **3.5 s** for a 2 s timeout.

## 7. Threats to validity

- **Scenario selection is ours.** The fourteen scenarios come from failures we have seen. A different list, for example one weighted toward interactive REPL use, would favour the PTY design (5.5), which is the only one here where a model can drive `python -i` or answer a prompt. The table measures robustness for *non-interactive agent work*; it doesn't prove there is no better design for interactive work.
- **Design choices, not only defects.** "No state between calls" (deepagents, Coder, Agents SDK) is a deliberate choice: stateless calls are easier to sandbox, retry and parallelise. Scenarios 1–3 measure what that choice costs a model that assumes a normal terminal, not a bug.
- **Five of the six adapters call the tool function, not a whole agent.** Only `pydanticai-coder` goes through a full agent. For the others we checked that the model-visible transformations live inside the layer we call (deepagents' truncation is in the backend; LangChain's in `ShellSession`; the Agents SDK's formatting in `ExecCommandTool.run`).
- **One OS.** Linux, bash 5, Python 3.11. The macOS paths of these SDKs (for example the Agents SDK's `sandbox-exec`) weren't exercised.

## 8. Decision input for v2

1. **Keep Polymath's terminal** and expose it as a native tool in whichever SDK v2 builds on. No prebuilt shell reaches it: the best score is 10/14, and every candidate fails at least one scenario that corrupts or loses information silently (lost tail, wrong cwd, orphaned or leaked processes).
2. **Adopt two ideas from the competitors**, both absent from v1:
   - *Detached long-running commands with handles* (Coder): an explicit `background: true` mode that returns PID, log path and status file. This is the right primitive for servers and watchers, and better than asking the model to hand-roll `nohup … &`.
   - *An optional PTY session with stdin writes* (Agents SDK) for genuinely interactive programs, kept off by default so accidental prompts still fail fast (scenario 6).
3. **Report upstream.** Worth filing: the `/`-cwd defect (5.6), deepagents' head-first truncation and timeout orphans (5.2), and LangChain's restart-on-timeout that discards partial output (5.1). Each has a reproduction above.
