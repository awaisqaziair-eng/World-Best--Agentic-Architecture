"""Hard tier: tasks designed to break shallow approaches and to exercise the
long-horizon paths (large corpora, delegation, strict specs, flaky services)."""

from __future__ import annotations

import http.server
import json
import random
import re
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..framework import Check, EvalTask, load_json, run, run_py, sha256, write

# ── H1: expression evaluator with Python semantics, no eval ─────────────────
EXPR_INSTR = """Implement `calc.py` with a function `evaluate(expression)` that evaluates an arithmetic expression string with exactly Python's semantics:
- integer and decimal literals (e.g. `42`, `3.5`), binary `+ - * / // % **`, unary `+` and `-`, and parentheses;
- the same precedence and associativity as Python (e.g. `-2**2 == -4`, `2**3**2 == 512`, `2**-1 == 0.5`, `7//-2 == -4`, `-7 % 3 == 2`);
- return an `int` whenever Python would return an int;
- division, floor division or modulo by zero must raise `ZeroDivisionError`; any malformed expression (e.g. `""`, `"2+"`, `"(1"`, `"1 2"`, `"3*/4"`) must raise `ValueError`;
- whitespace may appear between any tokens.
You must NOT use `eval`, `exec`, `compile` or the `ast` module — write the parser yourself. Test it thoroughly."""

EXPR_VERIFY = r"""
import random, re
src = open("calc.py").read()
# Builtins only: `re.compile(` / `obj.eval(` are legitimate (lookbehind excludes attribute access).
assert not re.search(r"(?<![\w.])(eval|exec|compile)\s*\(|^\s*import\s+ast\b|^\s*from\s+ast\s+import", src, re.M), "forbidden eval/exec/compile/ast"
from calc import evaluate
fixed = ["-2**2", "2**3**2", "2**-1", "7//-2", "-7 % 3", "(1+2)*3", "--3", "+-+4", "10/4", "10//4", "2*(3+4)**2", "1.5*2", "0.1+0.2", "  3 +\t4 ", "-(2+3)**2", "2**2**-1", "(-8)//3", "5%-3", "1-2-3", "2/2"]
for e in fixed:
    want = eval(e)
    got = evaluate(e)
    assert type(got) is type(want) and (got == want or abs(got - want) < 1e-9), f"{e!r}: got {got!r} want {want!r}"
rng = random.Random(4)
def gen(d):
    if d == 0 or rng.random() < 0.3:
        return rng.choice([str(rng.randint(0, 20)), f"{rng.randint(0, 9)}.{rng.randint(1, 9)}"])
    r = rng.random()
    if r < 0.15: return "-" + gen(d - 1)
    if r < 0.30: return "(" + gen(d - 1) + ")"
    op = rng.choice(["+", "-", "*", "/", "//", "%", "+", "-", "*"])
    return gen(d - 1) + f" {op} " + gen(d - 1)
checked = 0
for _ in range(600):
    e = gen(4)
    try:
        want = eval(e)
    except ZeroDivisionError:
        try:
            evaluate(e); raise AssertionError(f"{e!r} should raise ZeroDivisionError")
        except ZeroDivisionError:
            continue
    got = evaluate(e)
    assert type(got) is type(want) and abs(got - want) <= 1e-9 * max(1, abs(want)), f"{e!r}: got {got!r} want {want!r}"
    checked += 1
for bad in ["", "2+", "(1", "1 2", "3*/4", "()", "1)", "2**", "abc", "1..2", "* 3"]:
    try:
        evaluate(bad); raise AssertionError(f"{bad!r} should raise ValueError")
    except ValueError:
        pass
for z in ["1/0", "5//0", "5%0", "1/(2-2)"]:
    try:
        evaluate(z); raise AssertionError(f"{z!r} should raise ZeroDivisionError")
    except ZeroDivisionError:
        pass
print("OK", checked)
"""


def expr_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    rc, out, err = run_py(ws, EXPR_VERIFY, timeout=60)
    return rc == 0 and out.startswith("OK"), (out + err)[-500:]


# ── H2: Markdown → HTML mini-compiler ───────────────────────────────────────
MD_INSTR = """Write `md2html.py` with a function `convert(markdown)` that converts a Markdown subset to HTML (standard library only). Exact rules:
Blocks (separated by blank lines; output blocks joined with "\\n"):
- ATX headings `#` … `######` followed by a space → `<h1>text</h1>` … `<h6>text</h6>`.
- Fenced code blocks: a line starting with ``` (optionally followed by a language, e.g. ```python) up to the closing ``` line → `<pre><code>…</code></pre>`, or `<pre><code class="language-python">…</code></pre>` when a language is given. The content lines are HTML-escaped, joined with "\\n", and get NO other formatting. A fence may directly follow a paragraph line without a blank line.
- Unordered lists: consecutive lines starting with `- ` → `<ul><li>item</li><li>item</li></ul>`.
- Ordered lists: consecutive lines starting with `<number>. ` → `<ol><li>item</li></ol>`.
- Paragraphs: any other run of consecutive non-blank lines, joined with single spaces → `<p>text</p>`.
Inline formatting (headings, list items, paragraphs): first HTML-escape `&`, `<`, `>` (to `&amp;`, `&lt;`, `&gt;`); then `` `code` `` → `<code>code</code>` (its content receives no further formatting); `**bold**` → `<strong>bold</strong>`; `*italic*` → `<em>italic</em>`; `[text](url)` → `<a href="url">text</a>`.
Example: `convert("# Hi *there*\\n\\nA & B")` returns `"<h1>Hi <em>there</em></h1>\\n<p>A &amp; B</p>"`."""

MD_CASES = [
    ("# Hi *there*\n\nA & B", "<h1>Hi <em>there</em></h1>\n<p>A &amp; B</p>"),
    ("###### deep", "<h6>deep</h6>"),
    ("line one\nline two", "<p>line one line two</p>"),
    ("- a\n- **b**\n- `c*d*`", "<ul><li>a</li><li><strong>b</strong></li><li><code>c*d*</code></li></ul>"),
    ("1. first\n2. second\n10. tenth", "<ol><li>first</li><li>second</li><li>tenth</li></ol>"),
    ("```python\nif a < b and c > d:\n    print('**x**')\n```", '<pre><code class="language-python">if a &lt; b and c &gt; d:\n    print(\'**x**\')</code></pre>'),
    ("```\nplain\n```", "<pre><code>plain</code></pre>"),
    ("See [docs](http://x.io/a?b=1&c=2) now", '<p>See <a href="http://x.io/a?b=1&amp;c=2">docs</a> now</p>'),
    ("Use `a < b` and **bold *nested* text**", "<p>Use <code>a &lt; b</code> and <strong>bold <em>nested</em> text</strong></p>"),
    ("# T\n\n- x\n- y\n\npara\n\n1. z", "<h1>T</h1>\n<ul><li>x</li><li>y</li></ul>\n<p>para</p>\n<ol><li>z</li></ol>"),
    ("Intro:\n```\ncode here\n```\nafter", "<p>Intro:</p>\n<pre><code>code here</code></pre>\n<p>after</p>"),
    ("#not a heading", "<p>#not a heading</p>"),
]


def md_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    code = "from md2html import convert\nimport json, re, sys\ncases = json.loads(sys.stdin.read())\nnorm = lambda s: re.sub(r'>\\s+<', '><', s.strip())\nbad = []\nfor md, want in cases:\n    got = convert(md)\n    if norm(got) != norm(want): bad.append((md, got, want))\nprint(json.dumps(bad))\n"
    c = Check()
    import sys as _sys
    import tempfile, os
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
        fh.write("import sys; sys.path.insert(0, '.')\n" + code)
        path = fh.name
    try:
        rc, out, err = run([_sys.executable, path], ws, timeout=30, stdin=json.dumps(MD_CASES))
    finally:
        os.unlink(path)
    if not c.true(rc == 0, f"crashed: {err[-300:]}"):
        return c.result()
    bad = json.loads(out.strip().splitlines()[-1])
    c.true(not bad, f"{len(bad)}/{len(MD_CASES)} cases wrong, e.g. input={bad[0][0]!r} got={bad[0][1]!r} want={bad[0][2]!r}" if bad else "")
    return c.result()


# ── H3: sessionisation with mixed timezones ─────────────────────────────────
def sess_setup(ws: Path) -> dict:
    rng = random.Random(12)
    rows, per_user = [], defaultdict(list)
    base = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    offsets = [timedelta(0), timedelta(hours=5, minutes=30), timedelta(hours=-4), timedelta(hours=9), timedelta(hours=-7)]
    for u in range(1, 26):
        uid = f"u{u:03d}"
        t = base + timedelta(minutes=rng.randint(0, 600))
        for _ in range(rng.randint(1, 6)):  # sessions
            for _ in range(rng.randint(1, 8)):  # events in session
                per_user[uid].append(t)
                t += timedelta(seconds=rng.randint(10, 1500))
            t += timedelta(seconds=rng.randint(1801, 20000))
    for uid, ts in per_user.items():
        for t in ts:
            off = rng.choice(offsets)
            local = t.astimezone(timezone(off))
            s = local.isoformat()
            if off == timedelta(0) and rng.random() < 0.5:
                s = s.replace("+00:00", "Z")
            rows.append((uid, s, rng.choice(["view", "click", "search", "purchase"])))
    rng.shuffle(rows)
    write(ws, "events.csv", "user_id,timestamp,event\n" + "\n".join(",".join(r) for r in rows) + "\n")
    sessions = []
    for uid, ts in per_user.items():
        ts = sorted(ts)
        start = prev = ts[0]
        for t in ts[1:]:
            if (t - prev).total_seconds() > 1800:
                sessions.append((uid, (prev - start).total_seconds()))
                start = t
            prev = t
        sessions.append((uid, (prev - start).total_seconds()))
    cnt = defaultdict(int)
    for uid, _ in sessions:
        cnt[uid] += 1
    top = sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    return {"total": len(sessions), "avg": round(sum(d for _, d in sessions) / len(sessions), 1), "top": top, "long": sum(1 for _, d in sessions if d > 600)}


SESS_INSTR = """`events.csv` holds user events (`user_id,timestamp,event`) in random order. Timestamps are ISO-8601 with mixed UTC offsets (`Z`, `+05:30`, `-04:00`, …) — compare them as absolute instants. Split each user's events (sorted by time) into sessions: a new session starts when the gap since that user's previous event is MORE than 30 minutes. A session's duration is its last event time minus its first (0 for a single event). Write `metrics.json` with: `total_sessions` (int), `avg_session_seconds` (mean duration, rounded to 1 decimal), `top_user` (user with the most sessions; ties → smallest user_id), `long_sessions` (number of sessions longer than 600 seconds)."""


def sess_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    c = Check()
    try:
        m = load_json(ws / "metrics.json")
    except Exception as e:
        c.true(False, f"metrics.json unreadable: {e}")
        return c.result()
    c.eq(m.get("total_sessions"), ctx["total"], "total_sessions")
    c.close(m.get("avg_session_seconds"), ctx["avg"], "avg_session_seconds", tol=0.051)
    c.eq(m.get("top_user"), ctx["top"], "top_user")
    c.eq(m.get("long_sessions"), ctx["long"], "long_sessions")
    return c.result()


# ── H4: multi-module bug hunt ───────────────────────────────────────────────
LEDGER = {
    "ledger/__init__.py": "",
    "ledger/money.py": '''def to_cents(amount: str) -> int:
    """Parse a decimal amount string like "1,234.56" or "-5.10" into integer cents."""
    return int(float(amount.replace(",", "")) * 100)


def fmt(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100:,}.{cents % 100:02d}"
''',
    "ledger/fx.py": '''from datetime import date

# EUR→USD daily reference rates
RATES = {date(2026, 1, 30): 1.10, date(2026, 1, 31): 1.12, date(2026, 2, 2): 1.08, date(2026, 2, 27): 1.05}
_cache = {}


def rate(on: date) -> float:
    """Latest published rate on or before `on`."""
    days = sorted(d for d in RATES if d <= on)
    if not days:
        raise KeyError(f"no rate on or before {on}")
    return RATES[days[-1]]


def eur_to_usd_cents(cents: int, on: date) -> int:
    key = ("EUR", "USD")
    if key not in _cache:
        _cache[key] = rate(on)
    return round(cents * _cache[key])
''',
    "ledger/core.py": '''from dataclasses import dataclass
from datetime import date

from .fx import eur_to_usd_cents
from .money import to_cents


@dataclass
class Txn:
    day: date
    amount: str
    currency: str  # "USD" or "EUR"

    def usd_cents(self) -> int:
        cents = to_cents(self.amount)
        return cents if self.currency == "USD" else eur_to_usd_cents(cents, self.day)
''',
    "ledger/report.py": '''from datetime import date


def total_usd(txns, start: date, end: date) -> int:
    """Total in USD cents of transactions with start <= day <= end (both inclusive)."""
    return sum(t.usd_cents() for t in txns if start <= t.day < end)
''',
    "tests/__init__.py": "",
    "tests/test_ledger.py": '''import unittest
from datetime import date

from ledger.core import Txn
from ledger.money import fmt
from ledger.report import total_usd


class TestLedger(unittest.TestCase):
    def test_january_report(self):
        txns = [
            Txn(date(2026, 1, 30), "0.29", "USD"),
            Txn(date(2026, 1, 30), "100.00", "EUR"),
            Txn(date(2026, 1, 31), "100.00", "EUR"),
            Txn(date(2026, 1, 31), "19.99", "USD"),
            Txn(date(2026, 2, 2), "1,000.00", "EUR"),
        ]
        self.assertEqual(fmt(total_usd(txns, date(2026, 1, 30), date(2026, 1, 31))), "242.28")


if __name__ == "__main__":
    unittest.main()
''',
}

LEDGER_HIDDEN = r"""
from datetime import date
import importlib
import ledger.fx as fx
from ledger.money import to_cents
from ledger.report import total_usd
from ledger.core import Txn
for s, want in [("0.29", 29), ("19.99", 1999), ("1,234.56", 123456), ("-5.10", -510), ("0.07", 7), ("4.35", 435), ("1.005", 100)]:
    got = to_cents(s)
    assert got == want or (s == "1.005" and got in (100, 101)), (s, got, want)
fx._cache.clear()
assert fx.eur_to_usd_cents(10000, date(2026, 1, 30)) == 11000
assert fx.eur_to_usd_cents(10000, date(2026, 2, 27)) == 10500, "rate must depend on the date"
assert fx.eur_to_usd_cents(10000, date(2026, 2, 1)) == 11200
ts = [Txn(date(2026, 2, 27), "1.00", "USD"), Txn(date(2026, 2, 28), "2.00", "USD")]
assert total_usd(ts, date(2026, 2, 1), date(2026, 2, 28)) == 300
assert total_usd(ts, date(2026, 2, 28), date(2026, 2, 28)) == 200
print("OK")
"""


def ledger_setup(ws: Path) -> dict:
    for k, v in LEDGER.items():
        write(ws, k, v)
    return {"hash": sha256(ws / "tests/test_ledger.py")}


def ledger_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    c = Check()
    c.eq(sha256(ws / "tests/test_ledger.py"), ctx["hash"], "tests must not be modified")
    rc, out, err = run("python3 -m unittest discover -s tests -t .", ws)
    c.true(rc == 0, f"integration test fails: {err[-300:]}")
    rc, out, err = run_py(ws, LEDGER_HIDDEN)
    c.true(rc == 0, f"hidden: {(out + err)[-400:]}")
    return c.result()


LEDGER_INSTR = """The integration test in `tests/test_ledger.py` fails. Find and fix every bug in the `ledger` package so that it passes — the fixes must be correct in general (other inputs, dates and ranges), not special-cased for this test. Do not modify the tests. Run `python3 -m unittest discover -s tests -t . -v` to check."""


# ── H5: flaky service client ────────────────────────────────────────────────
class _FlakyHandler(http.server.BaseHTTPRequestHandler):
    counts: dict[str, int] = {}
    lock = threading.Lock()

    def do_GET(self):
        m = re.fullmatch(r"/items/(\d+)", self.path)
        with self.lock:
            self.counts[self.path] = self.counts.get(self.path, 0) + 1
            n = self.counts[self.path]
        if not m:
            return self._send(404, {"error": "not found"})
        i = int(m.group(1))
        if i >= 1000:
            return self._send(404, {"error": "no such item"})
        if n <= 2:
            return self._send(503, {"error": "busy, retry"})
        self._send(200, {"id": i, "value": i * 10})

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


def start_flaky() -> tuple[http.server.ThreadingHTTPServer, type]:
    handler = type("H", (_FlakyHandler,), {"counts": {}, "lock": threading.Lock()})
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, handler


def flaky_setup(ws: Path) -> dict:
    srv, _ = start_flaky()
    return {"url": f"http://127.0.0.1:{srv.server_address[1]}", "_server": srv}


FLAKY_INSTR = """An item service runs at {url} . `GET /items/<id>` returns JSON `{{"id": ..., "value": ...}}`, but the service is flaky: it often answers `503` and the request must then be retried; unknown ids return `404`.
Write `client.py` with a function `fetch_all(base_url, ids)` (standard library only) that returns a dict mapping each id to its `value`, or to `None` when the item does not exist (404). Retry a request that gets a 503 up to 5 times with exponential backoff starting at 0.05 s (0.05, 0.1, 0.2, …); never retry a 404. Test it against the running service."""


def flaky_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    c = Check()
    srv, handler = start_flaky()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        code = f"from client import fetch_all\nimport json\nprint(json.dumps(fetch_all({url!r}, [3, 1000, 17, 1234, 3])))\n"
        rc, out, err = run_py(ws, code, timeout=60)
        if not c.true(rc == 0, f"fetch_all crashed: {err[-300:]}"):
            return c.result()
        got = {int(k): v for k, v in json.loads(out.strip().splitlines()[-1]).items()}
        c.eq(got, {3: 30, 1000: None, 17: 170, 1234: None}, "fetch_all result")
        c.eq(handler.counts.get("/items/1000"), 1, "404s must not be retried (requests for id 1000)")
        c.true(handler.counts.get("/items/17", 0) >= 3, "503s must be retried")
    finally:
        srv.shutdown()
    return c.result()


# ── H6: explicit parallel delegation over a large corpus ────────────────────
PRODUCTS = {
    "Aurora": {"version": "4.2.1", "owner": "Team Nimbus"},
    "Borealis": {"version": "2.9.0", "owner": "Team Quasar"},
    "Cirrus": {"version": "7.0.3", "owner": "Team Helix"},
    "Draco": {"version": "1.14.2", "owner": "Team Nimbus"},
    "Eos": {"version": "3.3.0", "owner": "Team Vega"},
}


def deleg_setup(ws: Path) -> None:
    rng = random.Random(77)
    for p, facts in PRODUCTS.items():
        major, minor, patch = map(int, facts["version"].split("."))
        releases = [(f"{major}.{minor}.{max(0, patch - 1)}", "2026-03-10", "released"), (facts["version"], "2026-06-02", "released"), (f"{major}.{minor + 1}.0", "2026-10-01", "planned — not yet released"), (f"{major - 1}.9.9", "2025-11-20", "released")]
        rng.shuffle(releases)
        for i, (v, d, st) in enumerate(releases):
            write(ws, f"kb/{p.lower()}/release-{i}.md", f"# {p} release {v}\nDate: {d}\nStatus: {st}\n\nChanges: maintenance and fixes.\n")
        write(ws, f"kb/{p.lower()}/ownership.md", f"# {p} ownership\nCurrent owner: {facts['owner']} (since 2026-01).\nPrevious owner: Team Legacy.\n")
        write(ws, f"kb/{p.lower()}/draft-ownership-proposal.md", f"# DRAFT proposal (not approved)\nProposal to move {p} to Team Orion. Status: rejected.\n")
        for j in range(2):
            write(ws, f"kb/{p.lower()}/notes-{j}.md", f"# {p} notes {j}\nMeeting notes about roadmap item {rng.randint(100, 999)}.\n")


DELEG_INSTR = """The `kb/` directory has one sub-directory per product line (aurora, borealis, cirrus, draco, eos), each with release notes, ownership documents, drafts and meeting notes. For EACH product line determine (a) the current version — the newest version that has actually been released — and (b) the current owning team. Use the `delegate` tool to investigate the five product lines in parallel, one sub-agent per product line, then combine their reports. Write `products.json` mapping each product name (capitalised, e.g. `"Aurora"`) to `{"version": "<x.y.z>", "owner": "<team>"}`."""


def deleg_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    c = Check()
    try:
        got = load_json(ws / "products.json")
    except Exception as e:
        c.true(False, f"products.json unreadable: {e}")
        return c.result()
    for p, facts in PRODUCTS.items():
        g = got.get(p) or {}
        c.eq(str(g.get("version", "")).lstrip("v"), facts["version"], f"{p}.version")
        c.true(facts["owner"].lower() in str(g.get("owner", "")).lower(), f"{p}.owner {g.get('owner')!r} != {facts['owner']}")
    return c.result()


TASKS = [
    EvalTask("hard-expr-evaluator", "coding", EXPR_INSTR, lambda ws: None, expr_verify, max_turns=40, difficulty="hard"),
    EvalTask("hard-markdown", "coding", MD_INSTR, lambda ws: None, md_verify, max_turns=45, difficulty="hard"),
    EvalTask("hard-sessionize", "data", SESS_INSTR, sess_setup, sess_verify, max_turns=30, difficulty="hard"),
    EvalTask("hard-bug-hunt", "debugging", LEDGER_INSTR, ledger_setup, ledger_verify, max_turns=40, difficulty="hard"),
    EvalTask("hard-flaky-api", "web", FLAKY_INSTR, flaky_setup, flaky_verify, max_turns=30, difficulty="hard", teardown=lambda ctx: ctx.get("_server") and ctx["_server"].shutdown()),
    EvalTask("hard-delegate-research", "research", DELEG_INSTR, deleg_setup, deleg_verify, max_turns=30, difficulty="hard"),
]
