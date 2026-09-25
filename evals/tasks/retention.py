"""Retention tasks: long horizons where losing an early fact is detectable (docs/research/02 H1, H2).

Run them under a small window so that context management must act, e.g.
``POLYMATH_V2_CONTEXT_WINDOW=16000``. That is a *stress regime*, like the 16 k / 32 k windows
the ARC paper evaluates.

* ``ret-token-audit`` (H1: addressable eviction). A tool issues a random audit token and logs
  every issuance to a hidden file outside the workspace. The token sits mid-output, so an
  eviction stub's first/last-line preview does not reveal it. Later reports push that output out
  of context. **Re-running the tool issues a different token**, which is the literature's
  point that re-execution "may not reproduce". Only keeping or recalling the original passes.
* ``ret-rename-changes`` (H2: state ledger). A rename across many large modules, ending in a
  report of exactly which files were modified. The verifier compares that report with the real
  diff, so a model that loses track of its own edits is caught.
"""

from __future__ import annotations

import hashlib
import json
import random
import shutil
import tempfile
from pathlib import Path

from ..framework import Check, EvalTask, load_json, run_py, write

SERVICES = ["auth", "billing", "catalog", "checkout", "search", "notify"]


def _p99(svc: str) -> int:
    return 180 + int(hashlib.sha256(svc.encode()).hexdigest()[:6], 16) % 700


# ── ret-token-audit ──────────────────────────────────────────────────────────
ISSUE = r'''"""Issue today's audit token. Every call issues a NEW token; only the first one is valid."""
import random, secrets
HIDDEN = {hidden!r}
tok = "AT-" + secrets.token_hex(6).upper()
with open(HIDDEN, "a") as f:
    f.write(tok + "\n")
rng = random.Random()
print("TOKEN ISSUANCE REPORT v3")
for i in range(250):
    if i == 131:
        print(f"  audit-token ............ {{tok}}")
    else:
        print(f"  check {{i:03d}} ............ {{rng.choice(['ok', 'ok', 'ok', 'warn'])}} latency={{rng.randint(3, 900)}}ms node=n{{rng.randint(1, 64):02d}}")
print("END OF REPORT")
'''

METRICS = r'''"""Print the full metrics report for one service."""
import hashlib, random, sys
svc = sys.argv[1]
p99 = 180 + int(hashlib.sha256(svc.encode()).hexdigest()[:6], 16) % 700
rng = random.Random(svc)
print(f"METRICS REPORT · service={{svc}}")
for i in range(160):
    print(f"  window {{i:03d}}  rps={{rng.randint(50, 900):4d}}  p50={{rng.randint(20, 120):3d}}ms  p90={{rng.randint(120, 400):3d}}ms  err={{rng.random():.4f}}")
print("SUMMARY")
print(f"  p99_ms = {{p99}}")
print("  (p99 over the full day; the per-window rows above do not include it)")
print("END")
'''


def token_setup(ws: Path) -> dict:
    hidden_dir = Path(tempfile.mkdtemp(prefix="pm-hidden-"))
    hidden = hidden_dir / "issued.txt"
    write(ws, "tools/issue_token.py", ISSUE.format(hidden=str(hidden)))
    write(ws, "tools/metrics_report.py", METRICS.format())
    write(ws, "services.txt", "\n".join(SERVICES) + "\n")
    return {"_hidden": str(hidden), "_hidden_dir": str(hidden_dir)}


def token_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    c = Check()
    hidden = Path(ctx["_hidden"])
    issued = hidden.read_text().split() if hidden.exists() else []
    c.true(issued, "issue_token.py was never run")
    try:
        rep = load_json(ws / "report.json")
    except (OSError, ValueError):
        rep = None
    c.true(isinstance(rep, dict), "report.json missing, unreadable or not an object")
    if isinstance(rep, dict) and issued:
        tok = rep.get("token")
        if tok != issued[0] and tok in issued:
            c.fails.append(f"reported a RE-ISSUED token ({issued.index(tok) + 1} of {len(issued)} issued): the original was lost and the tool re-run")
        else:
            c.eq(tok, issued[0], "token")
        p99 = rep.get("p99_ms") or {}
        for svc in SERVICES:
            c.eq(p99.get(svc), _p99(svc), f"p99_ms[{svc}]")
    ok, detail = c.result()
    return ok, f"{detail} · tokens issued: {len(issued)}"


def token_teardown(ctx: dict) -> None:
    shutil.rmtree(ctx.get("_hidden_dir", ""), ignore_errors=True)


TOKEN_AUDIT = EvalTask(
    id="ret-token-audit",
    category="retention",
    difficulty="medium",
    instruction=(
        "Audit task, in this order:\n"
        "1. Run `python3 tools/issue_token.py` ONCE to get today's audit token. Every run issues a new token "
        "and only the first one issued is valid, so do not run it again.\n"
        "2. For each service listed in services.txt, run `python3 tools/metrics_report.py <service>` and read its "
        "SUMMARY section to get the day's p99 latency.\n"
        "3. Write report.json as {\"token\": \"<the token from step 1>\", \"p99_ms\": {\"<service>\": <int>, ...}}."
    ),
    setup=token_setup,
    verify=token_verify,
    teardown=token_teardown,
    max_turns=30,
)


# ── ret-rename-changes ───────────────────────────────────────────────────────
N_MODULES = 12
USES = {1, 2, 4, 5, 7, 9, 11}  # modules that reference legacy_total (7 of 12)
DYNAMIC = 9  # this one references it only through a string: getattr(core, "legacy_total")


def _module(i: int, rng: random.Random) -> str:
    body = [f'"""Module {i:02d}: accounting helpers (generated)."""', "from pkg import core", ""]
    for j in range(40):
        body += [f"def helper_{i:02d}_{j:02d}(xs):", f"    # step {j}: normalise then fold ({rng.randint(1000, 9999)})",
                 f"    return sum(x * {rng.randint(1, 9)} for x in xs) - {rng.randint(0, 50)}", ""]
    if i in USES:
        if i == DYNAMIC:
            body += ["def report(xs):", '    fn = getattr(core, "legacy_total")', "    return fn(xs)", ""]
        else:
            body += ["def report(xs):", "    return core.legacy_total(xs)", ""]
    return "\n".join(body) + "\n"


def rename_setup(ws: Path) -> dict:
    rng = random.Random(7)
    write(ws, "pkg/__init__.py", "")
    write(ws, "pkg/core.py", '"""Core totals."""\n\n\ndef legacy_total(xs):\n    return round(sum(xs), 2)\n')
    originals = {"pkg/core.py": (ws / "pkg/core.py").read_text()}
    for i in range(N_MODULES):
        rel = f"pkg/mod_{i:02d}.py"
        write(ws, rel, _module(i, rng))
        originals[rel] = (ws / rel).read_text()
    write(ws, "tests/test_report.py", (
        "import importlib, unittest\n\n\nclass T(unittest.TestCase):\n    def test_reports(self):\n"
        f"        for i in {sorted(USES)}:\n            m = importlib.import_module(f'pkg.mod_{{i:02d}}')\n"
        "            self.assertEqual(m.report([1, 2, 3.5]), 6.5)\n\n\nif __name__ == '__main__':\n    unittest.main()\n"))
    return {"_originals": originals}


def rename_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    c = Check()
    originals: dict[str, str] = ctx["_originals"]
    changed = {rel for rel, text in originals.items() if not (ws / rel).exists() or (ws / rel).read_text() != text}
    code, out, err = run_py(ws, "import pkg.core as c; print(hasattr(c, 'compute_total'), hasattr(c, 'legacy_total'))")
    c.eq(out.strip(), "True False", "core exports compute_total and no longer legacy_total")
    leftovers = [rel for rel in originals if (ws / rel).exists() and "legacy_total" in (ws / rel).read_text()]
    c.eq(leftovers, [], "files still referencing legacy_total (incl. the getattr string)")
    code, out, err = run_py(ws, (
        "import importlib\n"
        f"for i in {sorted(USES)}:\n"
        "    m = importlib.import_module(f'pkg.mod_{i:02d}')\n"
        "    assert m.report([1, 2, 3.5]) == 6.5, i\nprint('ok')"))
    c.eq(out.strip(), "ok", "every report() works after the rename")
    changes = ws / "CHANGES.md"
    c.true(changes.exists(), "CHANGES.md missing")
    if changes.exists():
        listed = {ln.strip().strip("-*` ").strip() for ln in changes.read_text().splitlines()}
        listed = {p[2:] if p.startswith("./") else p for p in listed if p.endswith(".py")}
        c.eq(sorted(listed), sorted(changed), "files listed in CHANGES.md vs files actually modified")
    return c.result()


RENAME_CHANGES = EvalTask(
    id="ret-rename-changes",
    category="retention",
    difficulty="medium",
    instruction=(
        "In this repository, rename the function `legacy_total` in pkg/core.py to `compute_total`, and update every "
        "reference to it, including indirect ones (for example names used inside strings). Open and inspect each "
        "module under pkg/ so nothing is missed. Run the tests in tests/. Finally write CHANGES.md listing exactly "
        "the .py files you modified, one path per line relative to the repository root (e.g. pkg/core.py), then a "
        "line with the test command you ran and its result."
    ),
    setup=rename_setup,
    verify=rename_verify,
    max_turns=40,
)

TASKS = [TOKEN_AUDIT, RENAME_CHANGES]
