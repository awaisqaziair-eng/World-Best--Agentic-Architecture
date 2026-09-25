"""Reference solutions: prove every task is solvable and every verifier accepts a
correct solution (no false negatives). Run: ``python -m evals.reference``.

Together with the untouched-workspace check (no false positives) this makes the
verifiers themselves tested code — an eval is only as honest as its checker.
"""

from __future__ import annotations

import configparser
import csv
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Callable

from polymath.types import RunResult

from .framework import run, write
from .tasks import ALL
from .tasks.data import SVG_DATA
from .tasks.knowledge import INVOICES, PUBLIC
from .tasks.ops import MAPPING
from .tasks.reasoning import DIGITS_TRUTH, GRID_TRUTH

SOL: dict[str, Callable[[Path, dict[str, Any]], str]] = {}


def sol(task_id: str):
    def deco(fn):
        SOL[task_id] = fn
        return fn

    return deco


@sol("code-lru-cache")
def _(ws, ctx):
    write(ws, "lru_cache.py", "from collections import OrderedDict\n\nclass LRUCache:\n    def __init__(self, capacity):\n        self.c = capacity; self.d = OrderedDict()\n    def get(self, k):\n        if k not in self.d: return -1\n        self.d.move_to_end(k); return self.d[k]\n    def put(self, k, v):\n        if k in self.d: self.d.move_to_end(k)\n        self.d[k] = v\n        if len(self.d) > self.c: self.d.popitem(last=False)\n")
    return "done"


@sol("code-intervals-tests")
def _(ws, ctx):
    write(ws, "intervals.py", "def merge_intervals(intervals):\n    out = []\n    for s, e in sorted(intervals):\n        if out and s <= out[-1][1]:\n            out[-1][1] = max(out[-1][1], e)\n        else:\n            out.append([s, e])\n    return out\n")
    tests = "import unittest\nfrom intervals import merge_intervals as m\nclass T(unittest.TestCase):\n" + "".join(f"    def test_{i}(self): self.assertEqual(m({a}), {b})\n" for i, (a, b) in enumerate([([], []), ([[1, 3], [3, 5]], [[1, 5]]), ([[5, 6], [1, 2]], [[1, 2], [5, 6]]), ([[1, 10], [2, 3]], [[1, 10]]), ([[1, 1]], [[1, 1]])]))
    write(ws, "test_intervals.py", tests)
    return "done"


@sol("code-cli-todo")
def _(ws, ctx):
    write(ws, "todo.py", '''import json, os, sys
F = "todos.json"
def load():
    return json.load(open(F)) if os.path.exists(F) else {"next": 1, "items": []}
def save(d): json.dump(d, open(F, "w"))
def main(a):
    d = load()
    if a[0] == "add":
        d["items"].append({"id": d["next"], "text": a[1], "done": False}); print(f"Added #{d['next']}"); d["next"] += 1
    elif a[0] == "list":
        print("\\n".join(f"{i['id']}. [{'x' if i['done'] else ' '}] {i['text']}" for i in d["items"]) or "No todos.")
    elif a[0] in ("done", "remove"):
        i = next((x for x in d["items"] if x["id"] == int(a[1])), None)
        if i is None: print(f"No todo #{a[1]}", file=sys.stderr); sys.exit(1)
        if a[0] == "done": i["done"] = True; print(f"Completed #{i['id']}")
        else: d["items"].remove(i); print(f"Removed #{i['id']}")
    save(d)
main(sys.argv[1:])
''')
    write(ws, "test_todo.py", "import unittest\nclass T(unittest.TestCase):\n    def test_x(self): self.assertTrue(True)\n")
    return "done"


@sol("code-optimize")
def _(ws, ctx):
    write(ws, "slow.py", "from collections import Counter\n\ndef count_pairs(nums, target):\n    seen = Counter(); n = 0\n    for x in nums:\n        n += seen[target - x]; seen[x] += 1\n    return n\n")
    out = run("python3 bench.py", ws)[1]
    return out


@sol("debug-inventory")
def _(ws, ctx):
    p = ws / "inventory/core.py"
    t = p.read_text().replace("items=[]):\n        self.items = items", "items=None):\n        self.items = list(items) if items is not None else []")
    t = t.replace("start = page * per_page", "start = (page - 1) * per_page")
    t = t.replace("    value = price * (1 - percent / 100)\n    return round(value, 2)", "    value = Decimal(str(price)) * (1 - Decimal(str(percent)) / 100)\n    return float(value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))")
    p.write_text(t)
    return "done"


@sol("debug-crash-hardening")
def _(ws, ctx):
    write(ws, "pipeline.py", '''import csv, json, re
ok = sk = 0; tot = 0.0
with open("data.csv", newline="") as f:
    r = csv.reader(f); next(r)
    for row in r:
        try:
            assert len(row) == 4 and row[1].strip() and re.fullmatch(r"\\d{4}-\\d{2}-\\d{2}", row[3])
            tot += float(row[2]); ok += 1
        except (AssertionError, ValueError):
            sk += 1
json.dump({"rows_processed": ok, "rows_skipped": sk, "total_amount": round(tot, 2)}, open("summary.json", "w"))
''')
    return "done"


@sol("refactor-rename")
def _(ws, ctx):
    for p in ws.rglob("*"):
        if p.is_file() and "calc_total" in p.read_text(errors="ignore"):
            p.write_text(p.read_text().replace("calc_total", "compute_total"))
    return "done"


@sol("ops-log-forensics")
def _(ws, ctx):
    c = Counter()
    for line in (ws / "access.log").read_text().splitlines():
        m = re.match(r'^(\S+) .*?" (\d{3}) ', line)
        if m and m.group(2).startswith("5"):
            c[m.group(1)] += 1
    top = sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    write(ws, "top5xx.txt", "\n".join(f"{ip} {n}" for ip, n in top) + "\n")
    return "done"


@sol("ops-organize-files")
def _(ws, ctx):
    man = {}
    for p in sorted(ws.iterdir()):
        if p.is_file():
            ext = p.name.rsplit(".", 1)[1].lower() if "." in p.name else ""
            d = next((k for k, v in MAPPING.items() if ext in v), "other")
            (ws / d).mkdir(exist_ok=True)
            shutil.move(str(p), ws / d / p.name)
            man[p.name] = f"{d}/{p.name}"
    write(ws, "manifest.json", json.dumps(man))
    return "done"


@sol("ops-archive-checksum")
def _(ws, ctx):
    run("tar --exclude='__pycache__' --exclude='*.log' -czf release.tar.gz project && sha256sum release.tar.gz > release.sha256", ws)
    return "done"


@sol("ops-http-service")
def _(ws, ctx):
    write(ws, "server.py", '''import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class H(BaseHTTPRequestHandler):
    def reply(self, code, obj):
        b = json.dumps(obj).encode(); self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        self.reply(200, {"status": "ok"}) if self.path == "/health" else self.reply(404, {"error": "not found"})
    def do_POST(self):
        if self.path != "/sum": return self.reply(404, {"error": "not found"})
        try:
            d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            assert isinstance(d.get("numbers"), list)
            self.reply(200, {"sum": sum(d["numbers"])})
        except Exception:
            self.reply(400, {"error": "bad request"})
    def log_message(self, *a): pass
ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
''')
    return "done"


@sol("git-feature-flow")
def _(ws, ctx):
    run("git checkout -qb feature/greet", ws)
    (ws / "app.py").write_text((ws / "app.py").read_text() + '\n\ndef greet(name):\n    return f"Hello, {name}!"\n')
    run("git commit -qam 'Add greet function' && git checkout -q main && git merge -q --no-ff feature/greet -m 'Merge feature/greet' && git tag v1.1.0", ws)
    return "done"


@sol("ops-config-audit")
def _(ws, ctx):
    out = []
    for p in (ws / "configs").glob("*.ini"):
        cp = configparser.ConfigParser(inline_comment_prefixes=(";",))
        cp.read(p)
        s = cp["service"]
        if int(s["timeout"]) > 30 and int(s["retries"]) < 2:
            out.append(s["name"])
    write(ws, "audit.txt", "\n".join(sorted(out)) + "\n")
    return "done"


@sol("data-sales-report")
def _(ws, ctx):
    rev, q2, mon = defaultdict(Decimal), Counter(), defaultdict(Decimal)
    ok = sk = 0
    for r in csv.DictReader(open(ws / "sales.csv")):
        try:
            u, pr = int(r["units"]), Decimal(r["unit_price"])
        except Exception:
            sk += 1
            continue
        ok += 1
        reg = r["region"].strip().title()
        rev[reg] += u * pr
        mon[r["date"][:7]] += u * pr
        if "2025-04-01" <= r["date"] <= "2025-06-30":
            q2[r["product"]] += u
    write(ws, "report.json", json.dumps({"revenue_by_region": {k: float(v.quantize(Decimal("0.01"), ROUND_HALF_UP)) for k, v in rev.items()}, "top_product_q2": q2.most_common(1)[0][0], "best_month": max(mon, key=mon.get), "valid_rows": ok, "skipped_rows": sk}))
    return "done"


@sol("data-sqlite-analytics")
def _(ws, ctx):
    db = sqlite3.connect(ws / "shop.db")
    q = "SELECT o.id, o.customer_id, SUM(i.quantity*i.unit_price) FROM orders o JOIN order_items i ON i.order_id=o.id WHERE o.status!='cancelled' AND o.order_date LIKE '2025-%' GROUP BY o.id"
    rows = db.execute(q).fetchall()
    spend = defaultdict(float)
    for _, c, v in rows:
        spend[c] += v
    names = dict(db.execute("SELECT id, name FROM customers"))
    top = [names[c] for c, _ in sorted(spend.items(), key=lambda kv: -kv[1])[:3]]
    none = db.execute("SELECT COUNT(*) FROM customers WHERE id NOT IN (SELECT customer_id FROM orders)").fetchone()[0]
    write(ws, "answer.json", json.dumps({"top_customers": top, "customers_without_orders": none, "avg_order_value_2025": round(sum(v for *_, v in rows) / len(rows), 2)}))
    return "done"


@sol("data-json-flatten")
def _(ws, ctx):
    users = json.load(open(ws / "users.json"))
    rows = []
    for u in users:
        a = u.get("address", {})
        tot = sum(Decimal(str(o["amount"])) for o in u["orders"])
        rows.append([u["id"], u["profile"]["name"], u["profile"]["contact"]["email"], a.get("city", ""), a.get("country", ""), len(u["orders"]), tot])
    rows.sort(key=lambda r: (-r[6], r[0]))
    with open(ws / "users.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "name", "email", "city", "country", "num_orders", "total_spent"])
        for r in rows:
            w.writerow([*r[:6], f"{r[6]:.2f}"])
    return "done"


@sol("data-svg-chart")
def _(ws, ctx):
    mx = max(SVG_DATA.values())
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="600" height="400"><text x="10" y="20">Revenue by Region</text>']
    for i, (k, v) in enumerate(SVG_DATA.items()):
        h = 300 * v / mx
        parts.append(f'<rect x="{50 + i * 100}" y="{350 - h}" width="60" height="{h:.3f}"/><text x="{50 + i * 100}" y="370">{k}</text>')
    write(ws, "chart.svg", "".join(parts) + "</svg>")
    return "done"


@sol("math-digit-sum")
def _(ws, ctx):
    write(ws, "answer.txt", f"{DIGITS_TRUTH}\n")
    return f"The answer is {DIGITS_TRUTH}."


@sol("math-grid-paths")
def _(ws, ctx):
    write(ws, "answer.txt", str(GRID_TRUTH))
    return str(GRID_TRUTH)


@sol("reason-schedule")
def _(ws, ctx):
    import itertools

    from .tasks.reasoning import TALKS, schedule_ok

    cells = [(r, s) for r in ("Hall", "Studio") for s in (1, 2, 3, 4)]
    talks = sorted(TALKS)
    for perm in itertools.permutations(cells):  # 8! = 40320: brute force proves solvability
        cand = {t: {"room": r, "slot": s} for t, (r, s) in zip(talks, perm)}
        if not schedule_ok(cand):
            write(ws, "schedule.json", json.dumps(cand))
            return "done"
    raise AssertionError("the scheduling puzzle has no solution")


@sol("qa-knowledge")
def _(ws, ctx):
    return "Heapsort runs in O(n log n) worst case, and it is not stable."


@sol("write-api-docs")
def _(ws, ctx):
    secs = "\n".join(f"### {fn}\nParameters: {', '.join(f'`{p}`' for p in ps)}.\nReturns a value. Raises `ValueError` on invalid input.\n" for fn, ps in PUBLIC.items())
    write(ws, "API.md", f"# geometry\n\n## Overview\nHelpers.\n\n## Installation\nCopy the file.\n\n## Usage\n```python\nimport geometry\n```\n\n## API Reference\n{secs}")
    return "done"


@sol("write-exec-summary")
def _(ws, ctx):
    txt = ("Helix Analytics delivered a strong second quarter, with quarterly revenue of $4.2M, up 18% year over year, while gross margin held at 78% "
           "during the platform migration. Customer health improved markedly: monthly churn fell to 3.1% after the new onboarding program, net revenue "
           "retention reached 112%, and NPS climbed to 47, the best in company history. The team shipped real-time dashboards, anomaly detection in beta "
           "and enterprise SSO, and the Q3 pipeline stands at $9.6M. The biggest risk is customer concentration: Northwind Logistics represents 31% of "
           "revenue and renews in November, so securing that renewal is the top priority, alongside cutting cloud spend by 10% and bringing anomaly "
           "detection to general availability. Guidance for Q3 is $4.4M to $4.6M in revenue, contingent on that renewal.")
    write(ws, "summary.md", txt)
    return "done"


@sol("extract-invoices")
def _(ws, ctx):
    write(ws, "invoices.json", json.dumps([{"invoice_id": a, "vendor": b, "date": c, "total": d, "currency": e} for a, b, c, d, e in INVOICES]))
    return "done"


@sol("research-multihop")
def _(ws, ctx):
    write(ws, "answer.json", json.dumps({"service": "SessionVault", "team": "Identity Platform", "lead": "Priya Raman", "retention_days": 14}))
    return "done"


@sol("research-parallel")
def _(ws, ctx):
    write(ws, "answers.json", json.dumps({"q1": 1200, "q2": 4, "q3": 7, "q4": 85}))
    return "done"


@sol("web-local-docs")
def _(ws, ctx):
    import urllib.request

    page = urllib.request.urlopen(ctx["url"] + "v2/orders.html", timeout=5).read().decode()
    rate = int(re.search(r"/v2/orders</td><td>(\d+)", page).group(1))
    write(ws, "answer.json", json.dumps({"rate_limit_per_minute": rate, "auth_header": "X-Api-Key"}))
    return "done"


@sol("general-ambiguous")
def _(ws, ctx):
    write(ws, "config.json", json.dumps({"port": 8080, "debug": False}))
    return "Created config.json (JSON assumed)."


@sol("hard-expr-evaluator")
def _(ws, ctx):
    write(ws, "calc.py", r'''import re

_TOK = re.compile(r"\s*(?:(\d+\.\d+|\d+)|(\*\*|//|[-+*/%()]))")


def _tokens(s):
    pos, out = 0, []
    s = s.rstrip()
    while pos < len(s):
        m = _TOK.match(s, pos)
        if not m or m.end() == pos:
            raise ValueError(f"bad token at {pos}")
        num, op = m.groups()
        out.append(("num", float(num) if "." in num else int(num)) if num else ("op", op))
        pos = m.end()
    return out


class _P:
    def __init__(self, toks):
        self.t, self.i = toks, 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def eat(self, op=None):
        k, v = self.peek()
        if k is None or (op is not None and v != op):
            raise ValueError("unexpected end or token")
        self.i += 1
        return k, v

    def expr(self):  # + -
        v = self.term()
        while self.peek()[1] in ("+", "-") and self.peek()[0] == "op":
            op = self.eat()[1]
            r = self.term()
            v = v + r if op == "+" else v - r
        return v

    def term(self):  # * / // %
        v = self.unary()
        while self.peek()[0] == "op" and self.peek()[1] in ("*", "/", "//", "%"):
            op = self.eat()[1]
            r = self.unary()
            v = v * r if op == "*" else v / r if op == "/" else v // r if op == "//" else v % r
        return v

    def unary(self):
        if self.peek() in (("op", "-"), ("op", "+")):
            op = self.eat()[1]
            v = self.unary()
            return -v if op == "-" else +v
        return self.power()

    def power(self):  # right-assoc, binds tighter than unary on its left
        base = self.atom()
        if self.peek() == ("op", "**"):
            self.eat()
            return base ** self.unary()
        return base

    def atom(self):
        k, v = self.peek()
        if k == "num":
            self.eat()
            return v
        if (k, v) == ("op", "("):
            self.eat()
            r = self.expr()
            self.eat(")")
            return r
        raise ValueError("expected number or (")


def evaluate(expression):
    p = _P(_tokens(expression))
    if not p.t:
        raise ValueError("empty")
    v = p.expr()
    if p.i != len(p.t):
        raise ValueError("trailing tokens")
    return v
''')
    return "done"


@sol("hard-markdown")
def _(ws, ctx):
    write(ws, "md2html.py", r'''import html, re


def _inline(t):
    t = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    codes = []
    t = re.sub(r"`([^`]*)`", lambda m: codes.append(m.group(1)) or f"\x00{len(codes) - 1}\x00", t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"\*(.+?)\*", r"<em>\1</em>", t)
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', t)
    return re.sub(r"\x00(\d+)\x00", lambda m: f"<code>{codes[int(m.group(1))]}</code>", t)


def convert(markdown):
    lines, out, i = markdown.split("\n"), [], 0
    para = []

    def flush():
        if para:
            out.append(f"<p>{_inline(' '.join(para))}</p>")
            para.clear()

    while i < len(lines):
        line = lines[i]
        if not line.strip():
            flush(); i += 1; continue
        if line.startswith("```"):
            flush()
            lang = line[3:].strip()
            body, i = [], i + 1
            while i < len(lines) and not lines[i].startswith("```"):
                body.append(lines[i].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")); i += 1
            i += 1
            cls = f' class="language-{lang}"' if lang else ""
            out.append(f"<pre><code{cls}>" + "\n".join(body) + "</code></pre>")
            continue
        m = re.match(r"(#{1,6}) (.*)", line)
        if m:
            flush(); n = len(m.group(1)); out.append(f"<h{n}>{_inline(m.group(2))}</h{n}>"); i += 1; continue
        for rx, tag in ((r"- (.*)", "ul"), (r"\d+\. (.*)", "ol")):
            if re.match(rx, line):
                flush(); items = []
                while i < len(lines) and re.match(rx, lines[i]):
                    items.append(f"<li>{_inline(re.match(rx, lines[i]).group(1))}</li>"); i += 1
                out.append(f"<{tag}>" + "".join(items) + f"</{tag}>")
                break
        else:
            para.append(line.strip()); i += 1
    flush()
    return "\n".join(out)
''')
    return "done"


@sol("hard-sessionize")
def _(ws, ctx):
    ev = defaultdict(list)
    for r in csv.DictReader(open(ws / "events.csv")):
        from datetime import datetime as _dt

        ev[r["user_id"]].append(_dt.fromisoformat(r["timestamp"]))
    sessions = []
    for u, ts in ev.items():
        ts.sort()
        s = p = ts[0]
        for t in ts[1:]:
            if (t - p).total_seconds() > 1800:
                sessions.append((u, (p - s).total_seconds())); s = t
            p = t
        sessions.append((u, (p - s).total_seconds()))
    cnt = Counter(u for u, _ in sessions)
    top = sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    write(ws, "metrics.json", json.dumps({"total_sessions": len(sessions), "avg_session_seconds": round(sum(d for _, d in sessions) / len(sessions), 1), "top_user": top, "long_sessions": sum(1 for _, d in sessions if d > 600)}))
    return "done"


@sol("hard-bug-hunt")
def _(ws, ctx):
    m = ws / "ledger/money.py"
    m.write_text(m.read_text().replace('    return int(float(amount.replace(",", "")) * 100)', '    from decimal import Decimal\n    return int(Decimal(amount.replace(",", "")) * 100)'))
    f = ws / "ledger/fx.py"
    f.write_text(f.read_text().replace('    key = ("EUR", "USD")', '    key = ("EUR", "USD", on)'))
    r = ws / "ledger/report.py"
    r.write_text(r.read_text().replace("start <= t.day < end", "start <= t.day <= end"))
    return "done"


@sol("hard-flaky-api")
def _(ws, ctx):
    write(ws, "client.py", '''import json, time, urllib.error, urllib.request


def fetch_all(base_url, ids):
    out = {}
    for i in ids:
        delay = 0.05
        for attempt in range(6):
            try:
                with urllib.request.urlopen(f"{base_url}/items/{i}", timeout=5) as r:
                    out[i] = json.loads(r.read())["value"]
                break
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    out[i] = None
                    break
                if e.code != 503 or attempt == 5:
                    raise
                time.sleep(delay); delay *= 2
    return out
''')
    return "done"


@sol("hard-delegate-research")
def _(ws, ctx):
    from .tasks.hard import PRODUCTS

    write(ws, "products.json", json.dumps(PRODUCTS))
    return "done"


def main() -> int:
    missing = [t.id for t in ALL if t.id not in SOL]
    if missing:
        print("tasks without reference solution:", missing)
    failures = 0
    for t in ALL:
        if t.id not in SOL:
            continue
        ws = Path(tempfile.mkdtemp()) / "ws"
        ws.mkdir()
        ctx = t.setup(ws) or {}
        try:
            answer = SOL[t.id](ws, ctx)
            ok, detail = t.verify(ws, RunResult("ref", "main", "completed", "finished", answer), ctx)
        except Exception as e:
            ok, detail = False, f"{type(e).__name__}: {e}"
        finally:
            if t.teardown:
                t.teardown(ctx)
            shutil.rmtree(ws.parent, ignore_errors=True)
        failures += not ok
        print(f"{'OK  ' if ok else 'FAIL'} {t.id:<24} {detail[:150]}")
    print(f"\n{len(SOL) - failures}/{len(SOL)} reference solutions accepted; {len(missing)} tasks lack one")
    return 1 if failures or missing else 0


if __name__ == "__main__":
    sys.exit(main())
