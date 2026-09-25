"""Terminal bake-off: the same adversarial scenarios against every shell implementation.

Shells compared (no real model involved):
  polymath          — polymath.tools.terminal.PersistentShell (v1 design)
  langchain         — langchain.agents.middleware.shell_tool.ShellSession (behind ShellToolMiddleware)
  deepagents        — deepagents.backends.LocalShellBackend.execute (the `execute` tool's backend)
  pydanticai-shell  — pydantic_ai_harness.shell.ShellToolset.run_command: the default `Shell` capability
                      tool (persist_cwd=True), with the dispatch-seam tail cap applied
  pydanticai-coder  — the `shell` tool that `Coder` actually registers, called THROUGH a real Agent by a
                      scripted FunctionModel, so dispatch, output limits and formatting are exactly as shipped
  openai-agents     — agents.sandbox `exec_command` (SandboxAgent's Shell capability) on a UnixLocal sandbox

Each adapter drives the layer that produces what the MODEL sees; where a layer sits above the one
called (a truncation seam), the adapter applies it. Two early runs got this wrong and were corrected
(docs/research/03-terminal-bakeoff.md, "Benchmark bugs found and fixed").

Each (shell, scenario) runs in its own subprocess with a hard wall limit, so a hang is
recorded as a failure instead of stalling the benchmark.

    python research/terminal_bakeoff.py            # run all, print table, write results json
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARD_LIMIT = 75  # seconds per scenario process

# (name, steps, check) — a step is "cmd" or ("cmd", timeout_s); check(outputs, codes, step_seconds) -> bool
SCENARIOS = {
    # any line: some shells prefix a "[stdout]" label (an earlier first-line-only check wrongly failed pydanticai here)
    "cd_persists": (["mkdir -p a/b && cd a/b", "pwd"], lambda o, c, t: any(ln.rstrip().endswith("/a/b") for ln in o[-1].splitlines())),
    "env_persists": (["export FOO=bar", "echo $FOO"], lambda o, c, t: "bar" in o[-1]),
    "function_persists": (["f(){ echo fn-$1; }", "f x"], lambda o, c, t: "fn-x" in o[-1]),
    "heredoc": (["python3 - <<'PY'\nprint(sum(range(10)))\nPY"], lambda o, c, t: "45" in o[0]),
    "unbalanced_quote": (["echo 'unclosed", "echo alive"], lambda o, c, t: "alive" in o[-1] and t[0] < 10),
    "stdin_read": (["read x; echo got=[$x]", "echo next"], lambda o, c, t: t[0] < 5 and "next" in o[-1]),
    "exit_code": (["false"], lambda o, c, t: c[0] == 1),
    "timeout_returns": ([("sleep 30", 2), "echo after"], lambda o, c, t: t[0] < 9 and "after" in o[-1]),
    "timeout_grandchild_pipe": ([("sh -c 'sleep 25'; echo done", 2), "echo after"], lambda o, c, t: t[0] < 9 and "after" in o[-1]),
    "background_survives_timeout": (["nohup sleep 60 > /dev/null 2>&1 & echo $! > bg.pid", ("sleep 30", 1), "kill -0 $(cat bg.pid) && echo bg-alive; kill $(cat bg.pid)"], lambda o, c, t: "bg-alive" in o[-1]),
    "exit_recovers": (["exit 7", "echo alive"], lambda o, c, t: "alive" in o[-1]),
    # Bounded = returned text far below the 20 MB produced. Tail kept = the final "END" (errors live at the end)
    # survives ANYWHERE in the result: the filler never contains "END", and some shells append handles/status
    # after the output (an earlier "END within the last 200 chars" check wrongly failed pydanticai-coder).
    "huge_output_bounded_tail_kept": (["yes abcdefghij | head -c 20000000; echo END"], lambda o, c, t: t[0] < 30 and len(o[0]) < 10_000_000 and "END" in o[0]),
    "progress_bar_collapsed": (["printf '10%%\\r50%%\\r100%%\\ndone\\n'"], lambda o, c, t: "10%" not in o[0] and "100%" in o[0]),
    "busy_loop_recovers": ([("while true; do :; done", 2), "echo ok"], lambda o, c, t: "ok" in o[-1] and sum(t) < 30),
}


# ── adapters: start(ws) -> handle; run(handle, cmd, timeout) -> (text, exit_code|None); close(handle) ──
def ad_polymath():
    sys.path.insert(0, str(ROOT))
    from polymath.tools.terminal import PersistentShell

    def start(ws):
        return PersistentShell(ws, scratch_dir=ws / ".sh")

    def run(h, cmd, timeout):
        r = h.run(cmd, timeout=timeout)
        return r.output, r.exit_code

    return start, run, lambda h: h.close()


def ad_langchain():
    from langchain.agents.middleware import HostExecutionPolicy
    from langchain.agents.middleware.shell_tool import ShellSession

    def start(ws):
        s = ShellSession(ws, HostExecutionPolicy(), ("/bin/bash",), dict(os.environ))
        s.start()
        return s

    def run(h, cmd, timeout):
        r = h.execute(cmd, timeout=timeout)
        return (r.output or ""), getattr(r, "exit_code", None)

    return start, run, lambda h: h.stop(2)


def ad_deepagents():
    from deepagents.backends import LocalShellBackend

    def start(ws):
        return LocalShellBackend(root_dir=str(ws), virtual_mode=False, timeout=180)

    def run(h, cmd, timeout):
        r = h.execute(cmd, timeout=int(max(1, timeout)))
        return (r.output or ""), r.exit_code

    return start, run, lambda h: None


def ad_pydanticai_shell():
    import asyncio

    from pydantic_ai_harness.shell import ShellToolset

    loop = asyncio.new_event_loop()

    def start(ws):
        # Same values the `Shell` capability passes by default, except: no command denylist, cwd persistence on.
        return ShellToolset(
            cwd=ws, allowed_commands=(), denied_commands=(), denied_operators=(), default_timeout=180,
            max_output_chars=50_000, persist_cwd=True, allow_interactive=False,
        )

    from pydantic_ai_harness._output import truncate_tail

    def run(h, cmd, timeout):
        try:
            text = loop.run_until_complete(h.run_command(cmd, timeout_seconds=timeout))
        except Exception as e:  # ModelRetry etc. surface as tool errors to a model
            text = f"[error] {type(e).__name__}: {e}"
        # Mirror ShellToolset.call_tool, the dispatch seam that caps what the MODEL sees (tail kept).
        # Calling run_command directly skips it; an earlier run of this benchmark did, and wrongly failed pydanticai.
        text = truncate_tail(text, 50_000)
        code = 0
        import re as _re

        m = _re.search(r"\[exit code: (-?\d+)\]", text)
        if m:
            code = int(m.group(1))
        return text, code

    return start, run, lambda h: None


def _exit_code(text: str, pattern: str, default: int | None) -> int | None:
    import re as _re

    m = _re.search(pattern, text)
    if not m:
        return default
    return None if m.group(1) == "null" else int(m.group(1))


def ad_pydanticai_coder():
    """Batch adapter: the whole scenario is ONE agent run whose scripted model issues each step."""
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelResponse, RetryPromptPart, TextPart, ToolCallPart, ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel
    from pydantic_ai_harness.coder import Coder

    def run_all(ws, steps):
        outs, codes, secs = [], [], []
        st = {"i": 0, "t": 0.0}

        def model_fn(messages, info):
            for part in getattr(messages[-1], "parts", []):
                if isinstance(part, (ToolReturnPart, RetryPromptPart)):
                    text = str(part.content) if isinstance(part, ToolReturnPart) else part.model_response()
                    secs.append(round(time.monotonic() - st["t"], 2))
                    outs.append(text)
                    codes.append(_exit_code(text, r'"exit_code":\s*(-?\d+|null)', None))
            if st["i"] >= len(steps):
                return ModelResponse(parts=[TextPart("done")])
            cmd, timeout = steps[st["i"]]
            st["i"] += 1
            st["t"] = time.monotonic()
            return ModelResponse(parts=[ToolCallPart("shell", {"command": cmd, "timeout": timeout})])

        async def stream_fn(messages, info):  # run_sync streams internally, so the script must stream too
            part = model_fn(messages, info).parts[0]
            if isinstance(part, TextPart):
                yield part.content
            else:
                yield {0: DeltaToolCall(name=part.tool_name, json_args=json.dumps(part.args))}

        Agent(FunctionModel(model_fn, stream_function=stream_fn), capabilities=[Coder(workspace=ws)]).run_sync("run the scripted commands")
        return outs, codes, secs

    return run_all


def ad_openai_agents():
    import asyncio

    from agents.sandbox import Manifest
    from agents.sandbox.capabilities.tools.shell_tool import ExecCommandArgs, ExecCommandTool
    from agents.sandbox.sandboxes import UnixLocalSandboxClient

    loop = asyncio.new_event_loop()

    def start(ws):
        async def make():
            session = await UnixLocalSandboxClient().create(manifest=Manifest(root=str(ws)))
            await session.start()
            return session

        session = loop.run_until_complete(make())
        return session, ExecCommandTool(session=session)

    def run(h, cmd, timeout):
        # yield_time_ms is the tool's own wait budget: after it the process keeps running under a session ID.
        text = loop.run_until_complete(h[1].run(ExecCommandArgs(cmd=cmd, yield_time_ms=int(timeout * 1000))))
        return text, _exit_code(text, r"Process exited with code (-?\d+)", None)

    def close(h):
        loop.run_until_complete(h[0].shutdown())

    return start, run, close


ADAPTERS = {
    "polymath": ad_polymath, "langchain": ad_langchain, "deepagents": ad_deepagents,
    "pydanticai-shell": ad_pydanticai_shell, "pydanticai-coder": ad_pydanticai_coder, "openai-agents": ad_openai_agents,
}


def _reap(ws: Path) -> int:
    """Kill processes a scenario left behind (cwd inside its workspace). Some designs keep timed-out
    commands running on purpose; without this a busy loop would outlive the benchmark."""
    import signal

    n = 0
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            if os.readlink(proc / "cwd").startswith(str(ws)):
                os.kill(int(proc.name), signal.SIGKILL)
                n += 1
        except OSError:
            continue
    return n


def run_one(adapter: str, scenario: str) -> dict:
    made = ADAPTERS[adapter]()
    steps, check = SCENARIOS[scenario]
    ws = Path(tempfile.mkdtemp(prefix=f"tb_{adapter}_"))
    norm = [(st, 20) if isinstance(st, str) else st for st in steps]
    if callable(made):  # batch adapter: runs the whole scenario itself
        try:
            outs, codes, secs = made(ws, norm)
            ok = len(outs) == len(norm) and bool(check(outs, codes, secs))
        except Exception as e:
            outs, codes, secs, ok = [f"[exception] {type(e).__name__}: {e}"], [None], [], False
        finally:
            reaped = _reap(ws)
        return {"adapter": adapter, "scenario": scenario, "passed": ok, "step_seconds": secs, "codes": codes,
                "reaped": reaped, "last_output": outs[-1][-160:] if outs else ""}
    start, run, close = made
    outs, codes, secs = [], [], []
    h = start(ws)
    try:
        for step in steps:
            cmd, timeout = (step, 20) if isinstance(step, str) else step
            t0 = time.monotonic()
            try:
                text, code = run(h, cmd, timeout)
            except Exception as e:
                text, code = f"[exception] {type(e).__name__}: {e}", None
            secs.append(round(time.monotonic() - t0, 2))
            outs.append(text if isinstance(text, str) else str(text))
            codes.append(code)
        ok = bool(check(outs, codes, secs))
    finally:
        try:
            close(h)
        except Exception:
            pass
        reaped = _reap(ws)
    return {"adapter": adapter, "scenario": scenario, "passed": ok, "step_seconds": secs, "codes": codes, "reaped": reaped,
            "last_output": outs[-1][-160:] if outs else ""}


def main() -> int:
    if len(sys.argv) == 4 and sys.argv[1] == "--one":
        print(json.dumps(run_one(sys.argv[2], sys.argv[3])))
        return 0
    jobs = [(a, s) for a in ADAPTERS for s in SCENARIOS]

    def launch(job):
        a, s = job
        t0 = time.monotonic()
        try:
            p = subprocess.run([sys.executable, __file__, "--one", a, s], capture_output=True, text=True, timeout=HARD_LIMIT, start_new_session=True)
            line = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ""
            rec = json.loads(line) if line.startswith("{") else {"adapter": a, "scenario": s, "passed": False, "error": (p.stderr or "")[-300:]}
        except subprocess.TimeoutExpired:
            rec = {"adapter": a, "scenario": s, "passed": False, "error": f"HANG: no result within {HARD_LIMIT}s"}
        rec["wall_s"] = round(time.monotonic() - t0, 1)
        return rec

    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(launch, jobs))
    out = ROOT / "research" / "results" / "terminal_bakeoff.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2))
    names = list(ADAPTERS)
    print("| Scenario | " + " | ".join(names) + " |")
    print("|---|" + "---|" * len(names))
    for s in SCENARIOS:
        row = []
        for a in names:
            r = next(x for x in results if x["adapter"] == a and x["scenario"] == s)
            secs = r.get("step_seconds") or []
            tsuffix = f" ({secs[0]:.0f}s)" if s.startswith(("timeout", "unbalanced", "stdin", "busy")) and secs else ""
            row.append(("✅" if r["passed"] else "❌") + (" hang" if "HANG" in str(r.get("error", "")) else "") + tsuffix)
        print(f"| {s} | " + " | ".join(row) + " |")
    print("| **total** | " + " | ".join(f"**{sum(1 for x in results if x['adapter'] == a and x['passed'])}/{len(SCENARIOS)}**" for a in names) + " |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
