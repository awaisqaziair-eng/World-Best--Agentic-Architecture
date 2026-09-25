"""Data work: dirty CSV analytics, SQL, JSON reshaping, visualisation."""

from __future__ import annotations

import csv
import json
import random
import sqlite3
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from ..framework import Check, EvalTask, load_json, write

REGIONS = ["North", "South", "East", "West", "Central"]
PRODUCTS = ["Widget", "Gadget", "Gizmo", "Doohickey", "Sprocket", "Flange", "Bracket"]


def sales_setup(ws: Path) -> dict:
    rng = random.Random(42)
    rows = [["date", "region", "product", "units", "unit_price"]]
    rev = defaultdict(Decimal)
    q2_units = defaultdict(int)
    month_rev = defaultdict(Decimal)
    valid = skipped = 0
    d0 = date(2025, 1, 1)
    for _ in range(3000):
        d = d0 + timedelta(days=rng.randint(0, 364))
        region = rng.choice(REGIONS)
        shown = rng.choice([region, region.lower(), f"  {region} ", region.upper()]) if rng.random() < 0.2 else region
        prod = rng.choice(PRODUCTS)
        units, price = rng.randint(1, 40), Decimal(f"{rng.uniform(2, 120):.2f}")
        u_s, p_s = str(units), str(price)
        r = rng.random()
        bad = False
        if r < 0.02:
            u_s, bad = "", True
        elif r < 0.035:
            u_s, bad = "N/A", True
        elif r < 0.05:
            p_s, bad = "", True
        elif r < 0.06:
            p_s, bad = "abc", True
        rows.append([d.isoformat(), shown, prod, u_s, p_s])
        if bad:
            skipped += 1
            continue
        valid += 1
        amount = units * price
        rev[region] += amount
        month_rev[d.strftime("%Y-%m")] += amount
        if 4 <= d.month <= 6:
            q2_units[prod] += units
    buf = [",".join(r) for r in rows]
    write(ws, "sales.csv", "\n".join(buf) + "\n")
    return {
        "revenue": {k: float(v.quantize(Decimal("0.01"), ROUND_HALF_UP)) for k, v in rev.items()},
        "top_q2": max(q2_units.items(), key=lambda kv: kv[1])[0],
        "best_month": max(month_rev.items(), key=lambda kv: kv[1])[0],
        "valid": valid,
        "skipped": skipped,
    }


SALES_INSTR = """Analyse `sales.csv` (columns: date, region, product, units, unit_price). Rules:
- A row is valid only if both `units` and `unit_price` are numeric; skip all other rows.
- Region names have inconsistent capitalisation and surrounding whitespace; normalise them to title case (e.g. `  north ` → `North`).
- Revenue of a row = units × unit_price.
Write `report.json` with exactly these keys:
- `revenue_by_region`: object mapping each region to its total revenue, rounded to 2 decimals
- `top_product_q2`: the product with the most units sold from April 1 to June 30, 2025 (inclusive)
- `best_month`: the month with the highest total revenue, formatted `YYYY-MM`
- `valid_rows` and `skipped_rows`: row counts (header excluded)"""


def sales_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    c = Check()
    try:
        r = load_json(ws / "report.json")
    except Exception as e:
        c.true(False, f"report.json unreadable: {e}")
        return c.result()
    got = r.get("revenue_by_region") or {}
    c.eq(sorted(got), sorted(ctx["revenue"]), "regions")
    for k, v in ctx["revenue"].items():
        c.close(got.get(k), v, f"revenue[{k}]", tol=0.02)
    c.eq(r.get("top_product_q2"), ctx["top_q2"], "top_product_q2")
    c.eq(r.get("best_month"), ctx["best_month"], "best_month")
    c.eq(r.get("valid_rows"), ctx["valid"], "valid_rows")
    c.eq(r.get("skipped_rows"), ctx["skipped"], "skipped_rows")
    return c.result()


# ── SQLite analytics ────────────────────────────────────────────────────────
def sql_setup(ws: Path) -> dict:
    rng = random.Random(7)
    db = sqlite3.connect(ws / "shop.db")
    db.executescript(
        "CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT, country TEXT);"
        "CREATE TABLE orders(id INTEGER PRIMARY KEY, customer_id INTEGER, order_date TEXT, status TEXT);"
        "CREATE TABLE order_items(order_id INTEGER, product TEXT, quantity INTEGER, unit_price REAL);"
    )
    first = ["Ada", "Grace", "Alan", "Linus", "Barbara", "Ken", "Margaret", "Dennis", "Frances", "Edsger", "Radia", "Tim", "Katherine", "John", "Hedy"]
    last = ["Lovelace", "Hopper", "Turing", "Torvalds", "Liskov", "Thompson", "Hamilton", "Ritchie", "Allen", "Dijkstra", "Perlman", "Berners-Lee", "Johnson", "McCarthy", "Lamarr"]
    for i in range(1, 41):
        db.execute("INSERT INTO customers VALUES (?,?,?)", (i, f"{first[i % 15]} {last[(i * 7) % 15]}-{i}", rng.choice(["US", "DE", "IN", "BR", "JP"])))
    oid = 0
    spend = defaultdict(float)
    order_vals = []
    with_orders = set()
    for cid in range(1, 41):
        if cid % 8 == 0:
            continue  # 5 customers without orders
        for _ in range(rng.randint(1, 8)):
            oid += 1
            year = rng.choice([2024, 2025, 2025, 2025])
            d = f"{year}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
            status = "cancelled" if rng.random() < 0.15 else rng.choice(["paid", "shipped", "delivered"])
            db.execute("INSERT INTO orders VALUES (?,?,?,?)", (oid, cid, d, status))
            with_orders.add(cid)
            total = 0.0
            for _ in range(rng.randint(1, 4)):
                q, p = rng.randint(1, 5), round(rng.uniform(5, 300), 2)
                db.execute("INSERT INTO order_items VALUES (?,?,?,?)", (oid, rng.choice(PRODUCTS), q, p))
                total += q * p
            if year == 2025 and status != "cancelled":
                spend[cid] += total
                order_vals.append(total)
    db.commit()
    names = dict(db.execute("SELECT id, name FROM customers"))
    db.close()
    top = sorted(spend.items(), key=lambda kv: -kv[1])[:3]
    return {"top": [names[c] for c, _ in top], "no_orders": 40 - len(with_orders), "aov": round(sum(order_vals) / len(order_vals), 2)}


SQL_INSTR = """`shop.db` is a SQLite database (tables `customers`, `orders`, `order_items`). Orders with status `cancelled` do not count as sales. An order's value is the sum of quantity × unit_price over its items. Answer:
1. The names of the 3 customers with the highest total value of non-cancelled orders placed in 2025, highest first.
2. How many customers have never placed any order (of any status)?
3. The average value of non-cancelled orders placed in 2025, rounded to 2 decimals.
Write `answer.json` with keys `top_customers` (list of 3 names), `customers_without_orders` (integer) and `avg_order_value_2025` (number)."""


def sql_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    c = Check()
    try:
        a = load_json(ws / "answer.json")
    except Exception as e:
        c.true(False, f"answer.json unreadable: {e}")
        return c.result()
    c.eq(a.get("top_customers"), ctx["top"], "top_customers")
    c.eq(a.get("customers_without_orders"), ctx["no_orders"], "customers_without_orders")
    c.close(a.get("avg_order_value_2025"), ctx["aov"], "avg_order_value_2025", tol=0.011)
    return c.result()


# ── JSON reshaping ──────────────────────────────────────────────────────────
def flat_setup(ws: Path) -> dict:
    rng = random.Random(3)
    users, rows = [], []
    for i in range(1, 41):
        name = f"User {chr(65 + i % 26)}{i}"
        u = {"id": i, "profile": {"name": name, "contact": {"email": f"user{i}@example.com"}}}
        if rng.random() > 0.15:
            u["address"] = {"city": rng.choice(["Lagos", "Lima", "Oslo", "Pune", "Kyoto"]), "country": rng.choice(["NG", "PE", "NO", "IN", "JP"])}
        orders = [{"id": f"o{i}-{k}", "amount": round(rng.uniform(1, 500), 2)} for k in range(rng.randint(0, 5))]
        u["orders"] = orders
        if rng.random() < 0.1:
            u["orders"].append({"id": "dup", "amount": 0})
        users.append(u)
        total = sum(Decimal(str(o["amount"])) for o in u["orders"])
        rows.append([str(i), name, f"user{i}@example.com", u.get("address", {}).get("city", ""), u.get("address", {}).get("country", ""), str(len(u["orders"])), f"{total:.2f}"])
    rows.sort(key=lambda r: (-Decimal(r[6]), int(r[0])))
    write(ws, "users.json", json.dumps(users, indent=2))
    return {"rows": rows}


FLAT_INSTR = """Convert `users.json` into `users.csv` with exactly this header: `id,name,email,city,country,num_orders,total_spent`.
- `name` and `email` come from the nested `profile`; `city`/`country` from `address` (empty strings when there is no address).
- `num_orders` is the number of entries in `orders`; `total_spent` is the sum of their `amount`, formatted with exactly 2 decimals.
- Sort rows by `total_spent` descending, then by `id` ascending."""


def flat_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    c = Check()
    p = ws / "users.csv"
    if not c.true(p.exists(), "users.csv missing"):
        return c.result()
    with open(p, newline="") as fh:
        got = list(csv.reader(fh))
    c.eq(got[0] if got else None, ["id", "name", "email", "city", "country", "num_orders", "total_spent"], "header")
    body = got[1:]
    c.eq(len(body), len(ctx["rows"]), "row count")
    mism = [i for i, (g, w) in enumerate(zip(body, ctx["rows"])) if g != w]
    c.true(not mism, f"{len(mism)} rows differ, first: got {body[mism[0]] if mism else ''} want {ctx['rows'][mism[0]] if mism else ''}")
    return c.result()


# ── Visualisation (SVG) ─────────────────────────────────────────────────────
SVG_DATA = {"North": 48210.5, "South": 31980.0, "East": 57340.25, "West": 22115.75, "Central": 40002.0}


def svg_setup(ws: Path) -> None:
    write(ws, "revenue.csv", "region,revenue\n" + "\n".join(f"{k},{v}" for k, v in SVG_DATA.items()) + "\n")


SVG_INSTR = """Using only the Python standard library (no plotting packages), create `chart.svg`: a vertical bar chart of the revenue per region in `revenue.csv`. Requirements: exactly one `<rect>` element per region and no other `<rect>` elements; bar heights proportional to revenue; a `<text>` label with each region's name; and a title text containing `Revenue by Region`. The file must be valid SVG/XML."""


def svg_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    c = Check()
    try:
        root = ET.parse(ws / "chart.svg").getroot()
    except Exception as e:
        c.true(False, f"chart.svg invalid/missing: {e}")
        return c.result()
    local = lambda t: t.rsplit("}", 1)[-1]
    rects = [el for el in root.iter() if local(el.tag) == "rect"]
    c.eq(len(rects), 5, "number of <rect>")
    texts = " ".join("".join(el.itertext()) for el in root.iter() if local(el.tag) == "text")
    for r in SVG_DATA:
        c.true(r in texts, f"label {r} missing")
    c.true("Revenue by Region" in texts, "title missing")
    try:
        hs = sorted(float(str(el.get("height")).replace("px", "")) for el in rects)
        vs = sorted(SVG_DATA.values())
        ratios = [h / v for h, v in zip(hs, vs)]
        c.true(max(ratios) / min(ratios) < 1.03, f"heights not proportional: {hs}")
    except Exception as e:
        c.true(False, f"bad rect heights: {e}")
    return c.result()


TASKS = [
    EvalTask("data-sales-report", "data", SALES_INSTR, sales_setup, sales_verify, max_turns=25),
    EvalTask("data-sqlite-analytics", "data", SQL_INSTR, sql_setup, sql_verify, max_turns=25),
    EvalTask("data-json-flatten", "data", FLAT_INSTR, flat_setup, flat_verify, max_turns=20),
    EvalTask("data-svg-chart", "data", SVG_INSTR, svg_setup, svg_verify, max_turns=20),
]
