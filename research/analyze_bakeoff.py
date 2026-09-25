"""Generate R5's tables from results.json files (published numbers are never hand-typed).

    python research/analyze_bakeoff.py DIR [DIR ...]            # stack table (one row per run dir)
    python research/analyze_bakeoff.py --paired A1,A2 B1,B2      # paired comparison of two stacks over repeats

The paired mode reports discordant task-repeat pairs and an exact two-sided sign test (McNemar's
exact form). With ~28 tasks per repeat, only large differences are detectable; the test says so.
"""

from __future__ import annotations

import json
import statistics as st
import sys
from math import comb
from pathlib import Path


def load(d: str) -> list[dict]:
    return json.loads((Path(d) / "results.json").read_text())["records"]


def tokens(r: dict) -> int:
    return int(r.get("input_tokens", 0) or 0) + int(r.get("output_tokens", 0) or 0)


INFRA = ("model_error", "APIError", "ModelHTTPError", "OpenAIRateLimitError", "RateLimitError")


def infra(r: dict) -> bool:
    err = str(r.get("error") or "")
    return (not r.get("passed")) and (r.get("stop_reason") in INFRA or any(k in err for k in ("429", "Too Many Requests", "overloaded", "gave up after")))


def stack_table(dirs: list[str]) -> str:
    rows = ["| Run | Stack | Model | Pass | Pass excl. infra | Median turns | Median tokens/task | Total tokens |", "|---|---|---|---|---|---|---|---|"]
    for d in dirs:
        meta = json.loads((Path(d) / "results.json").read_text())["meta"]
        recs = load(d)
        n, ok = len(recs), sum(bool(r.get("passed")) for r in recs)
        bad = sum(infra(r) for r in recs)
        rows.append(
            f"| {Path(d).name} | {meta.get('stack')} | {meta.get('model', '').split('/')[-1]} | **{ok}/{n}** ({ok / n:.0%}) | "
            f"{f'{ok}/{n - bad}' if bad else '–'} | {st.median(r.get('turns', 0) or 0 for r in recs):.0f} | "
            f"{st.median(tokens(r) for r in recs) / 1000:.1f} k | {sum(tokens(r) for r in recs) / 1000:,.0f} k |"
        )
    return "\n".join(rows)


def sign_test(b: int, c: int) -> float:
    """Exact two-sided sign test on discordant pairs (b = only A passed, c = only B passed)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * p)


def paired(a_dirs: list[str], b_dirs: list[str]) -> str:
    a = [(r["task"], i, r) for i, d in enumerate(a_dirs) for r in load(d)]
    b = {(r["task"], i): r for i, d in enumerate(b_dirs) for r in load(d)}
    pairs = [(t, i, ra, b[(t, i)]) for t, i, ra in a if (t, i) in b]
    only_a = [(t, i) for t, i, ra, rb in pairs if ra.get("passed") and not rb.get("passed")]
    only_b = [(t, i) for t, i, ra, rb in pairs if rb.get("passed") and not ra.get("passed")]
    both = sum(1 for _, _, ra, rb in pairs if ra.get("passed") and rb.get("passed"))
    neither = [(t, i) for t, i, ra, rb in pairs if not ra.get("passed") and not rb.get("passed")]
    ratios = [tokens(rb) / tokens(ra) for _, _, ra, rb in pairs if tokens(ra) and tokens(rb)]
    q = st.quantiles(ratios, n=4) if len(ratios) >= 4 else [float("nan")] * 3
    an, bn = Path(a_dirs[0]).name, Path(b_dirs[0]).name
    out = [
        f"Paired over {len(pairs)} task-repeat pairs ({len(a_dirs)} repeat(s)).",
        "",
        "| | Count |", "|---|---|",
        f"| both pass | {both} |",
        f"| only **{an}** passes | {len(only_a)} |",
        f"| only **{bn}** passes | {len(only_b)} |",
        f"| both fail | {len(neither)} |",
        "",
        f"Exact sign test on discordant pairs: p = {sign_test(len(only_a), len(only_b)):.2f}.",
        f"Token ratio {bn}/{an} per pair: median {st.median(ratios):.2f} (IQR {q[0]:.2f}–{q[2]:.2f}); totals {sum(tokens(ra) for *_, ra, _ in pairs) / 1000:,.0f} k vs {sum(tokens(rb) for *_, rb in pairs) / 1000:,.0f} k.",
        f"Only {an}: {sorted(only_a)}",
        f"Only {bn}: {sorted(only_b)}",
        f"Both fail: {sorted(neither)}",
    ]
    return "\n".join(out)


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--paired":
        print(paired(argv[1].split(","), argv[2].split(",")))
    else:
        print(stack_table(argv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
