"""Run Polymath v2 on a task:

    python -m polymath.v2 "fix the failing tests" --workspace ./repo [--model M] [--no-recall] [-v]

Model access: NVIDIA_NIM_API_KEY, and POLYMATH_BASE_URL (e.g. the egress governor at
http://127.0.0.1:8787/v1, started with `python -m polymath.egress`).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .agent import V2Options, build_agent


def _printer(verbose: bool):
    from pydantic_ai.messages import FunctionToolCallEvent, FunctionToolResultEvent

    async def handler(ctx: Any, events: Any) -> None:
        async for ev in events:
            if isinstance(ev, FunctionToolCallEvent):
                args = json.dumps(ev.part.args_as_dict(), default=str)
                print(f"  ▶ {ev.part.tool_name} {args[:160]}", file=sys.stderr, flush=True)
            elif isinstance(ev, FunctionToolResultEvent) and verbose:
                text = str(getattr(ev.part, "content", ""))
                print("    " + text.strip().split("\n")[-1][:160], file=sys.stderr, flush=True)

    return handler


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m polymath.v2", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("task")
    ap.add_argument("--workspace", default=".")
    ap.add_argument("--model", default=os.environ.get("POLYMATH_MODEL", "nvidia/nemotron-3-super-120b-a12b"))
    ap.add_argument("--max-requests", type=int, default=60)
    ap.add_argument("--context-window", type=int)
    ap.add_argument("--no-terminal", action="store_true", help="use the prebuilt Coder shell instead")
    ap.add_argument("--no-recall", action="store_true", help="use the prebuilt irreversible clearing instead")
    ap.add_argument("--no-ledger", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
    from pydantic_ai import UsageLimits

    opts = V2Options(terminal=not a.no_terminal, recall=not a.no_recall, ledger=not a.no_ledger, context_window=a.context_window)
    agent, tel = build_agent(a.model, Path(a.workspace), opts=opts)
    res = agent.run_sync(f"{a.task}\n\nWorking directory: {Path(a.workspace).resolve()}", usage_limits=UsageLimits(request_limit=a.max_requests),
                         model_settings={"temperature": 0.3}, event_stream_handler=_printer(a.verbose))
    print(res.output)
    u = res.usage
    recall = tel.recall_runs[-1].as_dict() if tel.recall_runs else {}
    print(f"\n[{u.requests} requests · {u.input_tokens or 0:,} in / {u.output_tokens or 0:,} out tokens · evictions {recall.get('evictions', 0)} · "
          f"reacquisitions {recall.get('reacquisitions', 0)} · ledger injections {len(tel.ledger_renders)}]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
