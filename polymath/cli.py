"""Command-line interface: ``polymath <command>`` (or ``python -m polymath``)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .config import load_config
from .events import EventLog
from .observability import load_spans, render_tree
from .types import Budget


def _runtime(args: argparse.Namespace, **extra):
    from .kernel.runtime import Runtime

    over = {
        "model": getattr(args, "model", None),
        "utility_model": getattr(args, "utility_model", None),
        "verbosity": 0 if getattr(args, "quiet", False) else (2 if getattr(args, "verbose", False) else None),
        "tool_protocol": getattr(args, "protocol", None),
        "verify": getattr(args, "verify_mode", None),
    }
    if getattr(args, "no_fallback", False):
        over["fallback_models"] = []
    over.update(extra)
    cfg = load_config(getattr(args, "config", None), **{k: v for k, v in over.items() if v is not None})
    if not cfg.api_key:
        sys.exit(f"error: environment variable {cfg.api_key_env} is not set (API key for {cfg.base_url})")
    return Runtime(cfg)


def _print_result(res, as_json: bool) -> int:
    if as_json:
        print(json.dumps(res.to_dict(), indent=2, ensure_ascii=False))
    else:
        print("\n" + (res.answer or f"(no answer — {res.state}: {res.error or res.stop_reason})"))
        print(f"\n[session {res.session_id} · {res.state}/{res.stop_reason} · {res.turns} turns · {res.usage.total:,} tokens · {res.duration_s:.1f}s]", file=sys.stderr)
    return 0 if res.state == "completed" else 1


def cmd_run(args: argparse.Namespace) -> int:
    rt = _runtime(args)
    instruction = args.task if args.task != "-" else sys.stdin.read()
    b = Budget(args.max_turns or rt.cfg.max_turns, rt.cfg.max_tokens, args.max_time or rt.cfg.max_wall_s, rt.cfg.max_tool_calls)
    ask = (lambda q: input(f"\n[agent asks] {q}\n> ")) if args.interactive else None
    res = rt.run(instruction, args.workspace, acceptance_criteria=args.criteria, verify_command=args.verify, budget=b, mode=args.mode, ask_user=ask)
    return _print_result(res, args.json)


def cmd_chat(args: argparse.Namespace) -> int:
    from .kernel.agent import Agent
    from .types import TaskSpec

    rt = _runtime(args)
    log = rt.new_session()
    agent = Agent(rt, log=log, workspace=args.workspace, ask_user=lambda q: input(f"\n[agent asks] {q}\n> "))
    print(f"Polymath {__version__} · model {rt.client.model} · session {log.session_id}\nType your task. Ctrl-D to exit.", file=sys.stderr)
    first = True
    try:
        while True:
            try:
                line = input("\n› ").strip()
            except EOFError:
                break
            if not line:
                continue
            res = agent.run(TaskSpec(instruction=line, workspace=str(Path(args.workspace).resolve()))) if first else agent.continue_with(line)
            first = False
            print("\n" + (res.answer or f"({res.state}: {res.error})"))
    finally:
        agent.close()
        log.close()
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    rt = _runtime(args)
    budget = None
    if args.max_turns or args.max_time:
        budget = Budget(args.max_turns or rt.cfg.max_turns, rt.cfg.max_tokens, args.max_time or rt.cfg.max_wall_s, rt.cfg.max_tool_calls)
    return _print_result(rt.resume(args.session, budget=budget), args.json)


def cmd_replay(args: argparse.Namespace) -> int:
    rt = _runtime(args)
    res, log = rt.replay(args.session, workspace=args.workspace, strict=args.strict)
    print(f"replayed into session {log.session_id}: {res.state}/{res.stop_reason}, {res.turns} turns", file=sys.stderr)
    return 0 if res.state == "completed" else 1


def cmd_sessions(args: argparse.Namespace) -> int:
    from .kernel.runtime import Runtime

    rt = Runtime(load_config(), client=_NullClient(), utility=False, console=False)
    for s in rt.list_sessions()[: args.limit]:
        print(f"{s['session_id']:<40} {s['state']:<11} {s['events']:>5} ev  {s['task']}")
    return 0


def cmd_trace(args: argparse.Namespace) -> int:
    cfg = load_config()
    d = cfg.sessions_path() / args.session
    if args.events:
        for e in EventLog.load(d / "events.jsonl"):
            summary = json.dumps(e.data, ensure_ascii=False)[:160]
            print(f"{e.seq:>5} {e.agent:<10} {e.type:<24} {summary}")
    else:
        print(render_tree(load_spans(d / "trace.jsonl")))
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    import urllib.request

    cfg = load_config()
    req = urllib.request.Request(f"{cfg.base_url}/models", headers={"Authorization": f"Bearer {cfg.api_key or ''}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    for m in sorted(x["id"] for x in data.get("data", [])):
        mark = "*" if m == cfg.model else ("u" if m == cfg.utility_model else ("f" if m in cfg.fallback_models else " "))
        print(f"{mark} {m}")
    return 0


def cmd_workflow(args: argparse.Namespace) -> int:
    from .kernel.workflows_builtin import WORKFLOWS

    if args.name not in WORKFLOWS:
        sys.exit(f"unknown workflow {args.name!r}; available: {sorted(WORKFLOWS)}")
    rt = _runtime(args)
    inputs = dict(kv.split("=", 1) for kv in args.input)
    res = WORKFLOWS[args.name]().run(rt, args.workspace, inputs, session_id=args.session)
    print(json.dumps({"session": res.session_id, "status": res.status, "skipped": res.skipped_from_journal, "steps": list(res.outputs)}, indent=2))
    return 0


class _NullClient:
    model = "none"

    def complete(self, request):  # pragma: no cover
        raise RuntimeError("no model configured")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="polymath", description="Polymath generalist agent harness")
    p.add_argument("--version", action="version", version=f"polymath {__version__}")
    p.add_argument("--config", help="path to polymath.toml")
    sub = p.add_subparsers(dest="cmd", required=True)

    def model_opts(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--model", help="primary model id")
        sp.add_argument("--utility-model", help="model for compaction/judging/routing")
        sp.add_argument("--no-fallback", action="store_true", help="disable model fail-over")
        sp.add_argument("--protocol", choices=["auto", "native", "text"], help="tool-call protocol")
        sp.add_argument("-q", "--quiet", action="store_true")
        sp.add_argument("-v", "--verbose", action="store_true")

    r = sub.add_parser("run", help="run a task to completion")
    r.add_argument("task", help="task instruction ('-' to read stdin)")
    r.add_argument("-w", "--workspace", default=".")
    r.add_argument("--verify", help="command that must exit 0 for the task to count as done")
    r.add_argument("--criteria", action="append", help="acceptance criterion (repeatable)")
    r.add_argument("--verify-mode", choices=["auto", "off", "always"])
    r.add_argument("--max-turns", type=int)
    r.add_argument("--max-time", type=float, help="wall-clock budget in seconds")
    r.add_argument("--mode", choices=["full", "minimal"], default="full")
    r.add_argument("-i", "--interactive", action="store_true", help="allow the agent to ask you questions")
    r.add_argument("--json", action="store_true")
    model_opts(r)
    r.set_defaults(fn=cmd_run)

    c = sub.add_parser("chat", help="interactive multi-turn session")
    c.add_argument("-w", "--workspace", default=".")
    model_opts(c)
    c.set_defaults(fn=cmd_chat)

    rs = sub.add_parser("resume", help="resume an interrupted or failed session")
    rs.add_argument("session")
    rs.add_argument("--max-turns", type=int, help="new turn budget (required to resume a budget-exhausted run)")
    rs.add_argument("--max-time", type=float, help="new wall-clock budget in seconds")
    rs.add_argument("--json", action="store_true")
    model_opts(rs)
    rs.set_defaults(fn=cmd_resume)

    rp = sub.add_parser("replay", help="re-run a session against its recorded model responses")
    rp.add_argument("session")
    rp.add_argument("-w", "--workspace")
    rp.add_argument("--strict", action="store_true", help="fail on the first request divergence")
    model_opts(rp)
    rp.set_defaults(fn=cmd_replay)

    ss = sub.add_parser("sessions", help="list sessions")
    ss.add_argument("--limit", type=int, default=30)
    ss.set_defaults(fn=cmd_sessions)

    t = sub.add_parser("trace", help="show a session's span tree (or raw events)")
    t.add_argument("session")
    t.add_argument("--events", action="store_true")
    t.set_defaults(fn=cmd_trace)

    m = sub.add_parser("models", help="list models available at the endpoint")
    m.set_defaults(fn=cmd_models)

    w = sub.add_parser("workflow", help="run a built-in deterministic workflow")
    w.add_argument("name")
    w.add_argument("-w", "--workspace", default=".")
    w.add_argument("--input", action="append", default=[], help="key=value (repeatable)")
    w.add_argument("--session", help="session id (re-running an existing one resumes it)")
    model_opts(w)
    w.set_defaults(fn=cmd_workflow)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.fn(args) or 0)
    except BrokenPipeError:  # e.g. `polymath trace S --events | head`
        sys.stderr.close()
        return 0
    except KeyboardInterrupt:
        print("\ninterrupted — resume later with `polymath resume <session>`", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
