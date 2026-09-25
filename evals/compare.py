"""Generate comparison tables from results.json files (so published numbers are
never hand-typed).

    python -m evals.compare evals/results/v1-glm53-full evals/results/v1-glm53-minimal ...
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path


def load(run_dir: str) -> dict:
    d = json.loads((Path(run_dir) / "results.json").read_text())
    d["name"] = Path(run_dir).name
    return d


def summary_row(d: dict) -> str:
    recs = d["records"]
    n = len(recs)
    ok = sum(r.get("passed", False) for r in recs)
    infra = [r for r in recs if not r.get("passed") and r.get("stop_reason") == "model_error"]
    toks = [r.get("input_tokens", 0) + r.get("output_tokens", 0) for r in recs]
    turns = [r.get("turns", 0) for r in recs]
    m = d["meta"]
    adj = f"{ok}/{n - len(infra)}" if infra else "–"
    return (
        f"| {d['name']} | {m['model']} | {m['mode']} | **{ok}/{n}** ({ok / n:.0%}) | {adj} | "
        f"{statistics.mean(turns):.1f} | {int(statistics.median(toks)):,} | {sum(toks):,} |"
    )


def per_task(runs: list[dict]) -> list[str]:
    tasks = sorted({r["task"] for d in runs for r in d["records"]})
    head = "| Task | " + " | ".join(d["name"] for d in runs) + " |"
    sep = "|---|" + "---|" * len(runs)
    lines = [head, sep]
    for t in tasks:
        cells = []
        for d in runs:
            r = next((x for x in d["records"] if x["task"] == t), None)
            if r is None:
                cells.append("·")
            else:
                mark = "✅" if r.get("passed") else ("⚠️" if r.get("stop_reason") == "model_error" else "❌")
                cells.append(f"{mark} {r.get('turns', '?')}t / {(r.get('input_tokens', 0) + r.get('output_tokens', 0)) / 1000:.0f}k")
        lines.append(f"| {t} | " + " | ".join(cells) + " |")
    return lines


def main(argv: list[str]) -> int:
    runs = [load(a) for a in argv]
    print("| Run | Model | Harness | Pass | Pass excl. infra | Mean turns | Median tokens/task | Total tokens |")
    print("|---|---|---|---|---|---|---|---|")
    for d in runs:
        print(summary_row(d))
    print()
    print("\n".join(per_task(runs)))
    print("\n✅ pass · ❌ fail · ⚠️ infrastructure failure (provider errors) · cells: turns / thousand tokens")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
