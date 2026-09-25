"""Writing, extraction, research over documents, web retrieval, ambiguity handling."""

from __future__ import annotations

import functools
import http.server
import json
import re
import threading
from pathlib import Path

from ..framework import Check, EvalTask, answer_text, load_json, write

# ── API documentation ───────────────────────────────────────────────────────
GEOMETRY = '''"""Small 2-D geometry helpers."""
import math


def _validate_points(points):
    if not points:
        raise ValueError("points must be a non-empty sequence")


def area_circle(radius):
    """Area of a circle. Raises ValueError if radius is negative."""
    if radius < 0:
        raise ValueError("radius must be >= 0")
    return math.pi * radius ** 2


def area_rectangle(width, height):
    """Area of a rectangle. Raises ValueError for negative sides."""
    if width < 0 or height < 0:
        raise ValueError("sides must be >= 0")
    return width * height


def distance(p1, p2):
    """Euclidean distance between two (x, y) points. Raises ValueError if a point is not 2-D."""
    if len(p1) != 2 or len(p2) != 2:
        raise ValueError("points must be 2-D")
    return math.dist(p1, p2)


def centroid(points):
    """Arithmetic mean of a non-empty list of (x, y) points. Raises ValueError on empty input."""
    _validate_points(points)
    xs, ys = zip(*points)
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def scale_polygon(points, factor, origin=(0, 0)):
    """Scale polygon vertices about `origin` by `factor`. Raises ValueError if factor <= 0 or points empty."""
    _validate_points(points)
    if factor <= 0:
        raise ValueError("factor must be > 0")
    ox, oy = origin
    return [(ox + (x - ox) * factor, oy + (y - oy) * factor) for x, y in points]
'''
PUBLIC = {"area_circle": ["radius"], "area_rectangle": ["width", "height"], "distance": ["p1", "p2"], "centroid": ["points"], "scale_polygon": ["points", "factor", "origin"]}

DOCS_INSTR = """Write `API.md` documenting the public API of `geometry.py`. Required structure: a level-1 title; the sections `## Overview`, `## Installation`, `## Usage` (containing at least one fenced Python code example) and `## API Reference`. Under `## API Reference`, include one `### <function_name>` subsection for every public function — and none for private helpers — each describing every parameter by name, the return value, and the exceptions it raises."""


def docs_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    c = Check()
    p = ws / "API.md"
    if not c.true(p.exists(), "API.md missing"):
        return c.result()
    t = p.read_text()
    c.true(re.search(r"(?m)^# \S", t), "level-1 title missing")
    for h in ["Overview", "Installation", "Usage", "API Reference"]:
        c.true(re.search(rf"(?m)^## {h}\s*$", t), f"section '## {h}' missing")
    usage = t.split("## Usage", 1)[-1].split("\n## ", 1)[0] if "## Usage" in t else ""
    c.true("```" in usage, "no fenced code example in Usage")
    subs = re.findall(r"(?m)^### `?([A-Za-z_][\w]*)`?(?:\(.*\))?\s*$", t)
    c.eq(sorted(set(subs)), sorted(PUBLIC), "### subsections")
    parts = re.split(r"(?m)^### ", t)
    for fn, params in PUBLIC.items():
        sec = next((s for s in parts if re.match(rf"`?{fn}\b", s)), "")
        for prm in params:
            c.true(re.search(rf"\b{prm}\b", sec), f"{fn}: parameter '{prm}' not described")
        c.true("ValueError" in sec, f"{fn}: ValueError not documented")
    return c.result()


# ── Constrained summary ─────────────────────────────────────────────────────
REPORT = """Quarterly Business Review — Q2 2026 — Helix Analytics

Executive context. Helix Analytics closed the second quarter with quarterly revenue of $4.2M, an increase of 18% year over year and 6% quarter over quarter. Gross margin held at 78%, slightly below the 80% target because of higher cloud costs during the migration to the new data platform. Operating expenses grew 9%, driven mainly by hiring in engineering (14 new hires) and customer success (6 new hires).

Customers. The customer base grew to 1,240 paying accounts. Monthly customer churn was 3.1%, down from 3.8% in Q1, following the launch of the onboarding program in April. Net revenue retention reached 112%. Our Net Promoter Score (NPS) rose to 47, the highest in company history, compared with 39 a year ago. Support ticket volume fell 12% while first-response time improved to 2.4 hours.

Product. We shipped 3 major features: real-time dashboards, the anomaly-detection module, and SSO for enterprise tiers. Adoption of real-time dashboards reached 41% of active accounts within six weeks. The anomaly-detection beta has 85 participating customers.

Pipeline. Qualified pipeline for Q3 stands at $9.6M, with an expected win rate of 27%. Average contract value increased to $38K.

Risks. The most significant risk is customer concentration: our largest customer, Northwind Logistics, accounts for 31% of revenue, and its contract is up for renewal in November. Secondary risks include rising cloud infrastructure costs (up 22% this quarter) and a tight hiring market for senior data engineers. A pending pricing change could also affect expansion revenue in the SMB segment.

Outlook. We expect Q3 revenue between $4.4M and $4.6M, contingent on the Northwind renewal and continued expansion in the mid-market segment. Priorities for Q3 are securing the Northwind renewal, reducing cloud spend by 10%, and general availability of anomaly detection.
"""


def summary_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    c = Check()
    p = ws / "summary.md"
    if not c.true(p.exists(), "summary.md missing"):
        return c.result()
    t = p.read_text().strip()
    words = len(re.findall(r"\S+", t))
    c.true(120 <= words <= 170, f"word count {words} not in 120-170")
    c.true(not re.search(r"(?m)^\s*(#|[-*•]\s|\d+[.)]\s)", t), "contains headings or list items")
    c.true("\n\n" not in t, "must be a single paragraph")
    c.true(re.search(r"\$4\.2\s?M|\$4\.2 million|4\.2 million", t), "revenue $4.2M missing")
    c.true("3.1%" in t or "3.1 %" in t or "3.1 percent" in t, "churn 3.1% missing")
    c.true(re.search(r"\b47\b", t), "NPS 47 missing")
    c.true(re.search(r"Northwind|concentration", t, re.I), "biggest risk (customer concentration / Northwind) missing")
    return c.result()


SUMMARY_INSTR = """Write `summary.md`: an executive summary of `report.txt` for the CEO. It must be 120–170 words, a single paragraph of prose (no headings, no bullet points, no numbered lists). It must state the quarterly revenue, the monthly customer churn rate and the NPS score exactly as given in the report, and name the single biggest risk the report identifies."""

# ── Structured extraction ───────────────────────────────────────────────────
EMAILS = """From: billing@acme-supplies.com
Subject: Invoice INV-2025-0042
Hi team, please find invoice INV-2025-0042 from Acme Supplies dated March 3, 2025. Amount due: $1,234.50 (USD). Thanks!
----
From: accounts@kontor.de
Subject: Rechnung / Invoice
Invoice number: KT-7781. Vendor: Kontor GmbH. Invoice date: 14/02/2025. Total: €1.250,75 (EUR, German number format).
----
From: finance@brightlane.co.uk
Invoice Ref BL/0093 — Brightlane Ltd — issued 2025-01-28 — total due GBP 980.00
----
From: sales@sakura-parts.jp
Sakura Parts invoice #SP-5520, date 05/03/2025, amount ¥120,000 (JPY).
----
From: noreply@cloudnine.io
Your CloudNine invoice CN-100-A for 2025-03-01 is ready. Total charged: USD 49.99.
----
From: ar@delta-logistics.com
Delta Logistics — Invoice DL-3310 — Date: February 20, 2025 — Balance: $12,000.00
----
From: facturation@parisprint.fr
Facture / Invoice PP-2025-17 from Paris Print SARL, dated 01/03/2025, total 3 400,00 € (EUR).
----
From: billing@acme-supplies.com
Subject: Invoice INV-2025-0051
Invoice INV-2025-0051 from Acme Supplies dated March 17, 2025. Amount due: $310.00 (USD).
----
From: invoices@northstar.ca
Northstar Consulting invoice NS-88 dated 2025-02-20, amount CAD 2,500.00
----
From: team@pixelforge.dev
PixelForge invoice PF-0007 (date: Jan 9, 2025) total $760.25 USD
"""
INVOICES = sorted(
    [
        ("INV-2025-0042", "Acme Supplies", "2025-03-03", 1234.50, "USD"),
        ("KT-7781", "Kontor GmbH", "2025-02-14", 1250.75, "EUR"),
        ("BL/0093", "Brightlane Ltd", "2025-01-28", 980.00, "GBP"),
        ("SP-5520", "Sakura Parts", "2025-03-05", 120000.0, "JPY"),
        ("CN-100-A", "CloudNine", "2025-03-01", 49.99, "USD"),
        ("DL-3310", "Delta Logistics", "2025-02-20", 12000.00, "USD"),
        ("PP-2025-17", "Paris Print SARL", "2025-03-01", 3400.00, "EUR"),
        ("INV-2025-0051", "Acme Supplies", "2025-03-17", 310.00, "USD"),
        ("NS-88", "Northstar Consulting", "2025-02-20", 2500.00, "CAD"),
        ("PF-0007", "PixelForge", "2025-01-09", 760.25, "USD"),
    ],
    key=lambda r: (r[2], r[0]),
)

EXTRACT_INSTR = """`emails.txt` contains 10 invoice emails in inconsistent formats. Numeric dates written with slashes use day/month/year order. Extract every invoice into `invoices.json`: a JSON list of objects with keys `invoice_id`, `vendor`, `date` (ISO `YYYY-MM-DD`), `total` (a number, e.g. 1250.75) and `currency` (ISO 4217 code such as USD, EUR, GBP, JPY, CAD). Sort the list by date ascending, then by invoice_id ascending."""


def extract_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    c = Check()
    try:
        got = load_json(ws / "invoices.json")
    except Exception as e:
        c.true(False, f"invoices.json unreadable: {e}")
        return c.result()
    c.eq(len(got), 10, "invoice count")
    c.eq([g.get("invoice_id") for g in got], [r[0] for r in INVOICES], "order of invoice_ids")
    by_id = {g.get("invoice_id"): g for g in got}
    for inv, vendor, d, total, cur in INVOICES:
        g = by_id.get(inv, {})
        c.eq(g.get("date"), d, f"{inv}.date")
        c.close(g.get("total"), total, f"{inv}.total", tol=0.005)
        c.eq(g.get("currency"), cur, f"{inv}.currency")
        c.true(vendor.lower().split()[0] in str(g.get("vendor", "")).lower(), f"{inv}.vendor {g.get('vendor')!r}")
    return c.result()


# ── Multi-hop research over a document corpus ───────────────────────────────
KB = {
    "services/sessionvault.md": "# SessionVault\nSessionVault is the service that stores user sessions for all web and mobile clients.\nOwner: Identity Platform team.\nBackend: Redis cluster `sess-prod` with nightly snapshots to object storage.\n",
    "services/tokencache.md": "# TokenCache\nTokenCache caches short-lived OAuth access tokens (TTL 5 minutes). It does NOT store user sessions; see SessionVault.\nOwner: Edge team.\n",
    "services/profilesvc.md": "# ProfileService\nStores user profile data (names, avatars, preferences).\nOwner: Accounts team.\nRetention: indefinite while the account is active.\n",
    "services/auditlog.md": "# AuditLog\nImmutable record of security-relevant events. Owner: Security Engineering. Retention 400 days.\n",
    "teams/identity-platform.md": "# Identity Platform team\nStatus: current (updated 2026-04-02)\nTeam lead: Priya Raman\nOn-call rotation: weekly. Services: SessionVault, LoginGateway.\n",
    "teams/identity-platform-2025.md": "# Identity Platform team (2025)\nStatus: SUPERSEDED by identity-platform.md\nTeam lead: Marco Diaz\n",
    "teams/edge.md": "# Edge team\nTeam lead: Sam Okafor. Services: TokenCache, CDN config.\n",
    "teams/accounts.md": "# Accounts team\nTeam lead: Lena Fischer.\n",
    "policies/data-retention-2025.md": "# Data retention policy (2025-01-15)\n- SessionVault: session records retained for 30 days.\n- AuditLog: 400 days.\n- Support tickets: 2 years.\n",
    "policies/data-retention-2026.md": "# Data retention policy — revision (effective 2026-06-01)\nThis revision supersedes the 2025 policy where they differ.\n- SessionVault: session records retained for 14 days (reduced from 30).\n- Support tickets: 18 months.\n",
    "policies/security.md": "# Security policy\nAll services storing personal data must encrypt at rest.\n",
    "runbooks/sessionvault-failover.md": "# SessionVault failover\nPromote the replica in the secondary region; sessions older than the retention window are not restored.\n",
    "architecture/overview.md": "# Architecture overview\nClients authenticate via LoginGateway, which issues tokens (cached by TokenCache) and creates sessions in SessionVault.\n",
    "misc/glossary.md": "# Glossary\nSession: server-side record of an authenticated user's login. Token: short-lived credential.\n",
}

KB_INSTR = """Using only the documents in `kb/`, determine: which service stores user sessions, which team owns that service, who currently leads that team, and the current retention period (in days) for that service's session records. When documents conflict, use the most recent authoritative information. Write `answer.json` with keys `service`, `team`, `lead` and `retention_days` (integer)."""


def kb_setup(ws: Path) -> None:
    for k, v in KB.items():
        write(ws, f"kb/{k}", v)


def kb_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    c = Check()
    try:
        a = load_json(ws / "answer.json")
    except Exception as e:
        c.true(False, f"answer.json unreadable: {e}")
        return c.result()
    c.true("sessionvault" in str(a.get("service", "")).lower(), f"service {a.get('service')!r}")
    c.true("identity platform" in str(a.get("team", "")).lower(), f"team {a.get('team')!r}")
    c.true("priya raman" in str(a.get("lead", "")).lower(), f"lead {a.get('lead')!r}")
    c.eq(a.get("retention_days"), 14, "retention_days")
    return c.result()


# ── Parallelisable multi-question research ──────────────────────────────────
KB2 = {}
for i in range(8):
    KB2[f"product/release-notes-{i}.md"] = f"# Release 3.{i}\nMinor fixes and performance work in module M{i}.\n"
KB2["product/release-notes-7.md"] = "# Release 3.7\nIntroduced offline mode. The maximum offline cache size is 750 MB.\n"
KB2["product/release-notes-8.md"] = "# Release 3.8\nOffline mode improvements: the maximum offline cache size is now 1200 MB (was 750 MB).\n"
for i in range(6):
    KB2[f"hr/policy-{i}.md"] = f"# HR policy {i}\nGeneral guidance section {i}.\n"
KB2["hr/policy-remote.md"] = "# Remote work policy\nEmployees may work remotely up to 3 days per week. Employees in the Lisbon office may work remotely up to 4 days per week.\n"
for i in range(6):
    KB2[f"infra/runbook-{i}.md"] = f"# Runbook {i}\nRoutine maintenance steps for cluster c{i}.\n"
KB2["infra/runbook-db.md"] = "# Database runbook\nPrimary database: PostgreSQL 16 on cluster `pg-main`. Point-in-time recovery window: 7 days. Backups run at 02:00 UTC.\n"
for i in range(5):
    KB2[f"finance/memo-{i}.md"] = f"# Finance memo {i}\nBudget notes for department D{i}.\n"
KB2["finance/travel.md"] = "# Travel policy\nThe per-diem allowance for international travel is EUR 85; domestic travel is EUR 45.\n"

KB2_INSTR = """The `kb/` directory holds ~30 internal documents across product, HR, infrastructure and finance. Answer these four independent questions using only those documents:
1. What is the current maximum offline cache size in MB?
2. How many days per week may employees in the Lisbon office work remotely?
3. How many days is the point-in-time recovery window of the primary database?
4. What is the per-diem allowance in EUR for international travel?
Write `answers.json` as `{"q1": <int>, "q2": <int>, "q3": <int>, "q4": <int>}`."""


def kb2_setup(ws: Path) -> None:
    for k, v in KB2.items():
        write(ws, f"kb/{k}", v)


def kb2_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    c = Check()
    try:
        a = load_json(ws / "answers.json")
    except Exception as e:
        c.true(False, f"answers.json unreadable: {e}")
        return c.result()
    for k, v in {"q1": 1200, "q2": 4, "q3": 7, "q4": 85}.items():
        c.eq(a.get(k), v, k)
    return c.result()


# ── Web retrieval from a local docs site ────────────────────────────────────
SITE = {
    "index.html": '<html><head><title>Acme API Docs</title></head><body><h1>Acme API</h1><ul><li><a href="v1/orders.html">Orders API v1 (deprecated)</a></li><li><a href="v2/index.html">API v2</a></li></ul></body></html>',
    "v1/orders.html": "<html><head><title>Orders v1</title></head><body><h1>Orders v1 (deprecated)</h1><p>Rate limit: 60 requests per minute. Auth header: X-Auth-Token.</p></body></html>",
    "v2/index.html": '<html><head><title>API v2</title></head><body><h1>API v2</h1><p>All v2 endpoints authenticate with the <code>X-Api-Key</code> header.</p><a href="orders.html">Orders</a> <a href="users.html">Users</a></body></html>',
    "v2/orders.html": "<html><head><title>Orders v2</title></head><body><h1>Orders v2</h1><table><tr><th>Endpoint</th><th>Limit</th></tr><tr><td>/v2/orders</td><td>120 requests/minute</td></tr><tr><td>/v2/orders/export</td><td>10 requests/minute</td></tr></table></body></html>",
    "v2/users.html": "<html><head><title>Users v2</title></head><body><p>Rate limit 300 requests/minute.</p></body></html>",
}


def web_setup(ws: Path) -> dict:
    site = ws.parent / (ws.name + "_site")
    for k, v in SITE.items():
        write(site, k, v)
    handler = functools.partial(_QuietHandler, directory=str(site))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return {"url": f"http://127.0.0.1:{srv.server_address[1]}/", "_server": srv}


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):  # noqa: D401
        pass


def web_teardown(ctx: dict) -> None:
    srv = ctx.get("_server")
    if srv:
        srv.shutdown()


WEB_INSTR = """Our API documentation is served at {url} . Find (a) the rate limit, in requests per minute, of the `/v2/orders` endpoint and (b) the HTTP header that carries the API key for v2 endpoints. Write `answer.json` as `{{"rate_limit_per_minute": <int>, "auth_header": "<header name>"}}`."""


def web_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    c = Check()
    try:
        a = load_json(ws / "answer.json")
    except Exception as e:
        c.true(False, f"answer.json unreadable: {e}")
        return c.result()
    c.eq(a.get("rate_limit_per_minute"), 120, "rate_limit_per_minute")
    c.eq(str(a.get("auth_header", "")).lower(), "x-api-key", "auth_header")
    return c.result()


# ── Underspecified request ──────────────────────────────────────────────────
def ambig_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    c = Check()
    hits = []
    for p in ws.rglob("*"):
        if p.is_file() and not any(part.startswith(".") for part in p.relative_to(ws).parts):
            t = p.read_text(errors="ignore")
            if "8080" in t and re.search(r"debug\W{0,6}(false|no|off|0)\b", t, re.I):
                hits.append(p.name)
    c.true(hits, "no config file with port 8080 and debug disabled")
    c.true(any(h.lower() in answer_text(res) for h in hits), "final answer should name the file created")
    return c.result()


TASKS = [
    EvalTask("write-api-docs", "writing", DOCS_INSTR, lambda ws: (write(ws, "geometry.py", GEOMETRY), None)[1], docs_verify, max_turns=20),
    EvalTask("write-exec-summary", "writing", SUMMARY_INSTR, lambda ws: (write(ws, "report.txt", REPORT), None)[1], summary_verify, max_turns=15),
    EvalTask("extract-invoices", "extraction", EXTRACT_INSTR, lambda ws: (write(ws, "emails.txt", EMAILS), None)[1], extract_verify, max_turns=20),
    EvalTask("research-multihop", "research", KB_INSTR, kb_setup, kb_verify, max_turns=20),
    EvalTask("research-parallel", "research", KB2_INSTR, kb2_setup, kb2_verify, max_turns=25),
    EvalTask("web-local-docs", "web", WEB_INSTR, web_setup, web_verify, max_turns=15, teardown=web_teardown),
    EvalTask("general-ambiguous", "general", "Create a configuration file for a web app that listens on port 8080 with debug mode disabled.", lambda ws: None, ambig_verify, max_turns=10, difficulty="easy"),
]
