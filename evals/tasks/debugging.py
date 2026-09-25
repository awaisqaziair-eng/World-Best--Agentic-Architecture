"""Debugging & refactoring: planted bugs, crash hardening, cross-file rename."""

from __future__ import annotations

import csv
import io
import json
import random
import re
from pathlib import Path

from ..framework import Check, EvalTask, load_json, run, run_py, sha256, write

# ── Planted bugs ────────────────────────────────────────────────────────────
CORE = '''from decimal import Decimal, ROUND_HALF_UP


class Inventory:
    def __init__(self, items=[]):
        self.items = items

    def add(self, name, qty):
        self.items.append({"name": name, "qty": qty})
        return self

    def total_quantity(self):
        return sum(i["qty"] for i in self.items)


def paginate(items, page, per_page):
    """Return the items on 1-based page number `page`."""
    start = page * per_page
    return items[start:start + per_page]


def apply_discount(price, percent):
    """Return `price` reduced by `percent` percent, rounded half-up to 2 decimals (as a float)."""
    value = price * (1 - percent / 100)
    return round(value, 2)
'''
TESTS = '''import unittest
from inventory import Inventory, paginate, apply_discount


class TestInventory(unittest.TestCase):
    def test_instances_are_independent(self):
        a = Inventory().add("apple", 3)
        b = Inventory()
        self.assertEqual(b.total_quantity(), 0)
        self.assertEqual(a.total_quantity(), 3)

    def test_first_page(self):
        self.assertEqual(paginate(list(range(10)), 1, 3), [0, 1, 2])

    def test_partial_last_page(self):
        self.assertEqual(paginate(list(range(10)), 4, 3), [9])

    def test_discount_rounds_half_up(self):
        self.assertEqual(apply_discount(0.125, 0), 0.13)
        self.assertEqual(apply_discount(2.675, 0), 2.68)

    def test_discount_percent(self):
        self.assertEqual(apply_discount(19.99, 15), 16.99)


if __name__ == "__main__":
    unittest.main()
'''
HIDDEN_INV = r"""
from inventory import Inventory, paginate, apply_discount
assert paginate(list(range(10)), 2, 3) == [3, 4, 5]
assert paginate(list(range(10)), 5, 3) == []
assert paginate([], 1, 10) == []
assert apply_discount(1.005, 0) == 1.01
assert apply_discount(50, 50) == 25.0
assert apply_discount(100, 12.5) == 87.5
x = Inventory(); y = Inventory(); x.add("a", 1)
assert y.items == [] and x.total_quantity() == 1
z = Inventory([{"name": "b", "qty": 2}]); assert z.total_quantity() == 2
print("OK")
"""


def inv_setup(ws: Path) -> dict:
    write(ws, "inventory/__init__.py", "from .core import Inventory, paginate, apply_discount\n")
    write(ws, "inventory/core.py", CORE)
    p = write(ws, "tests/test_inventory.py", TESTS)
    write(ws, "tests/__init__.py", "")
    return {"test_hash": sha256(p)}


def inv_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    c = Check()
    c.eq(sha256(ws / "tests/test_inventory.py"), ctx["test_hash"], "tests must not be modified")
    rc, out, err = run("python3 -m unittest discover -s tests -t .", ws)
    c.true(rc == 0, f"visible tests fail: {err[-300:]}")
    rc, out, err = run_py(ws, HIDDEN_INV)
    c.true(rc == 0, f"hidden tests: {(out + err)[-300:]}")
    return c.result()


# ── Crash hardening ─────────────────────────────────────────────────────────
PIPELINE = '''import csv
import json


def main():
    total = 0.0
    n = 0
    with open("data.csv", newline="") as f:
        for row in csv.DictReader(f):
            total += float(row["amount"])
            n += 1
    with open("summary.json", "w") as f:
        json.dump({"rows_processed": n, "total_amount": round(total, 2)}, f)


main()
'''


def crash_setup(ws: Path) -> dict:
    rng = random.Random(5)
    lines = ["id,customer,amount,date"]
    valid, skipped, total = 0, 0, 0.0
    customers = ["acme", "globex", "initech", "umbrella", "hooli", "stark", "wayne"]
    for i in range(1, 601):
        kind = rng.random()
        cust = rng.choice(customers)
        amt = round(rng.uniform(1, 999), 2)
        date = f"2025-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
        if kind < 0.03:
            lines.append(f"{i},{cust},abc,{date}"); skipped += 1
        elif kind < 0.05:
            lines.append(f"{i},{cust},,{date}"); skipped += 1
        elif kind < 0.07:
            lines.append(f"{i},,{amt},{date}"); skipped += 1
        elif kind < 0.09:
            lines.append(f"{i},{cust},{amt},{rng.randint(1, 28)}/{rng.randint(1, 12)}/2025"); skipped += 1
        elif kind < 0.10:
            lines.append(f"{i},{cust},{amt}"); skipped += 1
        elif kind < 0.11:
            lines.append(f"{i},{cust},{amt},{date},extra"); skipped += 1
        else:
            lines.append(f"{i},{cust},{amt},{date}"); valid += 1; total += amt
    write(ws, "data.csv", "\n".join(lines) + "\n")
    write(ws, "pipeline.py", PIPELINE)
    return {"valid": valid, "skipped": skipped, "total": round(total, 2)}


CRASH_INSTR = """`python3 pipeline.py` crashes on `data.csv`. Make it robust: a data row is valid only if it has exactly 4 fields, a non-empty customer, an amount that parses as a number, and a date in `YYYY-MM-DD` format. Invalid rows must be skipped (not fixed). The script must write `summary.json` with the keys `rows_processed` (number of valid rows), `rows_skipped` (number of invalid data rows, header excluded) and `total_amount` (sum of the valid amounts, rounded to 2 decimals). Run it to produce `summary.json`."""


def crash_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    c = Check()
    (ws / "summary.json").unlink(missing_ok=True)
    rc, out, err = run("python3 pipeline.py", ws)
    c.eq(rc, 0, f"pipeline.py exit code ({err[-200:]})")
    try:
        s = load_json(ws / "summary.json")
    except Exception as e:
        c.true(False, f"summary.json unreadable: {e}")
        return c.result()
    c.eq(s.get("rows_processed"), ctx["valid"], "rows_processed")
    c.eq(s.get("rows_skipped"), ctx["skipped"], "rows_skipped")
    c.close(s.get("total_amount"), ctx["total"], "total_amount")
    return c.result()


# ── Cross-file rename ───────────────────────────────────────────────────────
def rename_setup(ws: Path) -> None:
    write(ws, "shop/__init__.py", "")
    write(ws, "shop/utils.py", 'def calc_total(items):\n    """Sum of price * qty over items."""\n    return round(sum(i["price"] * i["qty"] for i in items), 2)\n')
    write(ws, "shop/cart.py", "from .utils import calc_total\n\n\nclass Cart:\n    def __init__(self):\n        self.items = []\n\n    def add(self, price, qty=1):\n        self.items.append({\"price\": price, \"qty\": qty})\n\n    def total(self):\n        return calc_total(self.items)\n")
    write(ws, "shop/orders.py", "from shop import utils\n\n\ndef order_total(order):\n    subtotal = utils.calc_total(order[\"items\"])\n    return round(subtotal + order.get(\"shipping\", 0), 2)\n")
    write(ws, "shop/report.py", "from .utils import calc_total as ct\n\n\ndef summarize(orders):\n    return {\"orders\": len(orders), \"revenue\": round(sum(ct(o[\"items\"]) for o in orders), 2)}\n")
    write(ws, "tests/__init__.py", "")
    write(ws, "tests/test_shop.py", "import unittest\nfrom shop.utils import calc_total\nfrom shop.cart import Cart\nfrom shop.orders import order_total\nfrom shop.report import summarize\n\n\nclass T(unittest.TestCase):\n    def test_calc_total(self):\n        self.assertEqual(calc_total([{\"price\": 2.5, \"qty\": 2}]), 5.0)\n\n    def test_cart(self):\n        c = Cart(); c.add(1.25, 4); self.assertEqual(c.total(), 5.0)\n\n    def test_order(self):\n        self.assertEqual(order_total({\"items\": [{\"price\": 10, \"qty\": 1}], \"shipping\": 4.99}), 14.99)\n\n    def test_report(self):\n        self.assertEqual(summarize([{\"items\": [{\"price\": 3, \"qty\": 3}]}]), {\"orders\": 1, \"revenue\": 9})\n\n\nif __name__ == \"__main__\":\n    unittest.main()\n")
    write(ws, "README.md", "# shop\n\nUse `calc_total(items)` from `shop.utils` to compute totals.\n")


RENAME_INSTR = """Rename the function `calc_total` to `compute_total` everywhere in this project: its definition, every import and call site (including aliased imports), the tests and the README. Behaviour must not change, the test suite (`python3 -m unittest discover -s tests -t .`) must pass, and no occurrence of `calc_total` may remain anywhere in the project."""


def rename_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    c = Check()
    left = [str(p.relative_to(ws)) for p in ws.rglob("*") if p.is_file() and "__pycache__" not in p.parts and "calc_total" in p.read_text(errors="ignore")]
    c.eq(left, [], "files still mentioning calc_total")
    rc, out, err = run("python3 -m unittest discover -s tests -t .", ws)
    c.true(rc == 0, f"tests fail: {err[-300:]}")
    rc, out, err = run_py(ws, "from shop.utils import compute_total\nfrom shop.report import summarize\nassert compute_total([{'price': 1.5, 'qty': 2}]) == 3.0\nassert summarize([{'items': [{'price': 2, 'qty': 2}]}])['revenue'] == 4\nprint('OK')")
    c.true(rc == 0, f"hidden: {(out + err)[-200:]}")
    return c.result()


TASKS = [
    EvalTask("debug-inventory", "debugging", "The tests in `tests/` are failing. Find and fix the bugs in the `inventory` package so that all tests pass (`python3 -m unittest discover -s tests -t . -v`). Do not modify the tests.", inv_setup, inv_verify, max_turns=30),
    EvalTask("debug-crash-hardening", "debugging", CRASH_INSTR, crash_setup, crash_verify, max_turns=25),
    EvalTask("refactor-rename", "coding", RENAME_INSTR, rename_setup, rename_verify, max_turns=30),
]
