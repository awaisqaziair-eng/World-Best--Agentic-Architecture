"""Evaluation runner.

    python -m evals.runner --model z-ai/glm-5.3 --tasks core --workers 3 --out /tmp/runs/glm53

Each task runs in a fresh workspace; the hidden verifier decides pass/fail.
Writes ``results.json`` (every record) and ``report.md`` (summary tables).
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from polymath.config import load_config  # noqa: E402
from polymath.kernel.runtime import Runtime  # noqa: E402
from polymath.types import Budget  # noqa: E402

from .framework import EvalTask  # noqa: E402
from .tasks import select  # noqa: E402

_print_lock = threading.Lock()
STACK = "polymath-v1"
NO_WALL_BUDGET = False  # set from --stack


def run_one(rt: Runtime, task: EvalTask, out: Path, rep: int, mode: str) -> dict[str, Any]:
    ws = out / "workspaces" / f"{task.id}__r{rep}"
    shutil.rmtree(ws, ignore_errors=True)
    ws.mkdir(parents=True)
    rec: dict[str, Any] = {"task": task.id, "category": task.category, "difficulty": task.difficulty, "repeat": rep}
    ctx: dict[str, Any] = {}
    t0 = time.monotonic()
    try:
        ctx = task.setup(ws) or {}
        instruction = task.instruction.format(**{k: v for k, v in ctx.items() if not k.startswith("_") and isinstance(v, (str, int, float))}) if "{" in task.instruction and ctx else task.instruction
        if STACK != "polymath-v1":
            from .backends import BACKENDS

            res = BACKENDS[STACK](instruction, ws, model=rt.cfg.model, max_turns=task.max_turns)
            rec.update(
                state=res.state,
                stop_reason=res.stop_reason,
                turns=res.turns,
                input_tokens=res.input_tokens,
                output_tokens=res.output_tokens,
                duration_s=round(res.duration_s, 1),
                answer=(res.answer or "")[:600],
                error=res.error,
            )
            if res.extra:
                rec["extra"] = {k: v for k, v in res.extra.items() if k != "trace"}
        else:
            res = rt.run(
                instruction,
                ws,
                acceptance_criteria=task.criteria,
                # --no-wall-budget: the prebuilt stacks have no wall-clock budget, so under provider queueing a
                # 900 s budget fails only v1 (defect 19). Stack comparisons use the same turn budget for all.
                budget=Budget(max_turns=task.max_turns, max_wall_s=(10**9 if NO_WALL_BUDGET else task.max_wall_s), max_tokens=4_000_000, max_tool_calls=task.max_turns * 4),
                mode=mode,
                session_id=f"{task.id}__r{rep}",
            )
            rec.update(
                state=res.state,
                stop_reason=res.stop_reason,
                turns=res.turns,
                tool_calls=res.tool_calls,
                input_tokens=res.usage.input_tokens,
                output_tokens=res.usage.output_tokens,
                requests=res.usage.requests,
                duration_s=round(res.duration_s, 1),
                models=res.models_used,
                verifications=len(res.verification),
                answer=(res.answer or "")[:600],
                error=res.error,
            )
        try:
            passed, detail = task.verify(ws, res, ctx)
        except Exception as e:
            passed, detail = False, f"verifier crashed: {type(e).__name__}: {e}"
        rec.update(passed=bool(passed), detail=detail)
        sess = rt.session_dir(f"{task.id}__r{rep}")  # only polymath-v1 writes sessions; others: flags stay False/0
        rec["delegated"] = any('"subagent.spawned"' in l for l in open(sess / "events.jsonl", encoding="utf-8")) if (sess / "events.jsonl").exists() else False
        rec["compactions"] = sum(1 for l in open(sess / "events.jsonl", encoding="utf-8") if '"context.compacted"' in l) if (sess / "events.jsonl").exists() else 0
    except Exception as e:
        rec.update(passed=False, state="harness_error", detail=f"{type(e).__name__}: {e}", trace=traceback.format_exc()[-2000:])
    finally:
        if task.teardown:
            try:
                task.teardown(ctx)
            except Exception:
                pass
    rec["wall_s"] = round(time.monotonic() - t0, 1)
    with _print_lock:
        mark = "PASS" if rec.get("passed") else "FAIL"
        print(
            f"[{mark}] {task.id:<24} r{rep} {rec.get('state', '?'):<10} turns={rec.get('turns', '-'):<3} "
            f"tok={rec.get('input_tokens', 0) + rec.get('output_tokens', 0):>8,} {rec['wall_s']:>6.1f}s  {'' if rec.get('passed') else rec.get('detail', '')[:160]}",
            flush=True,
        )
    return rec


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    def agg(rs: list[dict[str, Any]]) -> dict[str, Any]:
        n = len(rs)
        ok = sum(1 for r in rs if r.get("passed"))
        toks = [r.get("input_tokens", 0) + r.get("output_tokens", 0) for r in rs]
        return {
            "n": n,
            "passed": ok,
            "pass_rate": round(ok / n, 3) if n else 0.0,
            "median_turns": statistics.median([r.get("turns", 0) for r in rs]) if rs else 0,
            "median_tokens": int(statistics.median(toks)) if toks else 0,
            "total_tokens": sum(toks),
            "median_wall_s": statistics.median([r.get("wall_s", 0) for r in rs]) if rs else 0,
        }

    cats = sorted({r["category"] for r in records})
    return {"overall": agg(records), "by_category": {c: agg([r for r in records if r["category"] == c]) for c in cats}}


def write_report(out: Path, meta: dict[str, Any], records: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    o = summary["overall"]
    lines = [
        f"# Evaluation report — {meta['model']} ({meta['mode']}){' — ' + meta['label'] if meta.get('label') else ''}",
        "",
        f"- Date: {meta['started']}  ·  tasks: {o['n']}  ·  workers: {meta['workers']}  ·  harness mode: `{meta['mode']}`",
        f"- **Pass rate: {o['passed']}/{o['n']} = {o['pass_rate']:.1%}**",
        f"- Median turns {o['median_turns']} · median tokens {o['median_tokens']:,} · total tokens {o['total_tokens']:,} · median wall {o['median_wall_s']}s",
        "",
        "## By category",
        "",
        "| Category | Passed | Pass rate | Median turns | Median tokens |",
        "|---|---|---|---|---|",
    ]
    for c, a in summary["by_category"].items():
        lines.append(f"| {c} | {a['passed']}/{a['n']} | {a['pass_rate']:.0%} | {a['median_turns']} | {a['median_tokens']:,} |")
    lines += ["", "## Tasks", "", "| Task | Result | State / stop | Turns | Tokens | Wall s | Notes |", "|---|---|---|---|---|---|---|"]
    for r in sorted(records, key=lambda r: (r["category"], r["task"], r["repeat"])):
        note = "" if r.get("passed") else r.get("detail", "")[:140].replace("|", "\\|").replace("\n", " ")
        extra = " (delegated)" if r.get("delegated") else ""
        lines.append(
            f"| {r['task']}{extra} | {'✅' if r.get('passed') else '❌'} | {r.get('state')}/{r.get('stop_reason', '')} | {r.get('turns', '')} | "
            f"{r.get('input_tokens', 0) + r.get('output_tokens', 0):,} | {r.get('wall_s')} | {note} |"
        )
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Polymath evaluation runner")
    ap.add_argument("--model")
    ap.add_argument("--utility-model")
    ap.add_argument("--tasks", default="core", help="core (default) | hard | all | categories | task ids, comma-separated")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--mode", choices=["full", "minimal"], default="full")
    ap.add_argument("--verify-mode", choices=["auto", "off", "always"])
    ap.add_argument("--router", choices=["heuristic", "llm", "off"])
    ap.add_argument("--no-fallback", action="store_true")
    ap.add_argument("--context-window", type=int, help="override the model window (context-stress runs)")
    ap.add_argument("--max-output-tokens", type=int, help="override the per-response output budget")
    ap.add_argument("--label", default="", help="free-text label stored in results (e.g. harness version)")
    ap.add_argument("--no-wall-budget", action="store_true", help="polymath-v1: no wall-clock budget (fair vs stacks without one)")
    ap.add_argument("--stack", default="polymath-v1", help="agent stack: polymath-v1 | deepagents | pydanticai-coder | openai-agents | polymath-v2")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    global STACK, NO_WALL_BUDGET
    STACK = args.stack
    NO_WALL_BUDGET = args.no_wall_budget

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    over: dict[str, Any] = {"sessions_dir": str(out / "sessions"), "memory_path": str(out / "memory.jsonl"), "verbosity": 0}
    for k, v in {"model": args.model, "utility_model": args.utility_model, "verify": args.verify_mode, "router": args.router, "context_window": args.context_window, "max_output_tokens": args.max_output_tokens}.items():
        if v:
            over[k] = v
    if args.no_fallback:
        over["fallback_models"] = []
    cfg = load_config(**over)
    if args.max_output_tokens:  # an explicit override beats per-model defaults (e.g. GLM-5.3's 32k)
        mp = {k: dict(v) for k, v in cfg.model_params.items()}
        mp.setdefault(cfg.model, {})["max_output_tokens"] = args.max_output_tokens
        cfg = cfg.replace(model_params=mp)
    rt = Runtime(cfg, console=False)
    tasks = select(args.tasks)
    meta = {"stack": args.stack, "label": args.label, "no_wall_budget": args.no_wall_budget, "context_window": cfg.context_window, "max_output_tokens": cfg.max_output_for(cfg.model), "router": cfg.router, "model": cfg.model, "mode": args.mode, "workers": args.workers, "started": datetime.now(timezone.utc).isoformat(timespec="seconds"), "fallbacks": cfg.fallback_models, "utility_model": cfg.utility_model, "n_tasks": len(tasks), "repeat": args.repeat}
    print(f"Running {len(tasks)} tasks × {args.repeat} on {cfg.model} (mode={args.mode}, workers={args.workers})", flush=True)
    jobs = [(t, r) for r in range(1, args.repeat + 1) for t in tasks]
    records: list[dict[str, Any]] = []
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(run_one, rt, t, out, r, args.mode) for t, r in jobs]
        for f in as_completed(futs):
            records.append(f.result())
            (out / "results.json").write_text(json.dumps({"meta": meta, "records": records}, indent=2), encoding="utf-8")
    summary = summarize(records)
    meta["wall_s"] = round(time.monotonic() - t0, 1)
    (out / "results.json").write_text(json.dumps({"meta": meta, "summary": summary, "records": records}, indent=2), encoding="utf-8")
    write_report(out, meta, records, summary)
    o = summary["overall"]
    print(f"\nPASS {o['passed']}/{o['n']} ({o['pass_rate']:.1%}) · total tokens {o['total_tokens']:,} · wall {meta['wall_s']}s\nreport: {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
