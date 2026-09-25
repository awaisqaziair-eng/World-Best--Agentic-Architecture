"""Terminal / operations work: log forensics, file organisation, packaging, services, git, config audit."""

from __future__ import annotations

import configparser
import hashlib
import json
import random
import re
import socket
import subprocess
import tarfile
import time
import urllib.request
from collections import Counter
from pathlib import Path

from ..framework import Check, EvalTask, load_json, run, sha256, write

# ── Log forensics ───────────────────────────────────────────────────────────
def log_setup(ws: Path) -> dict:
    rng = random.Random(21)
    ips = [f"10.{rng.randint(0, 3)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}" for _ in range(250)]
    hot = ips[:12]
    paths = ["/", "/login", "/api/v1/items", "/api/v1/orders", "/static/app.js", "/status/503", "/errors/500.html", "/health"]
    lines, c5 = [], Counter()
    for i in range(20000):
        ip = rng.choice(hot) if rng.random() < 0.18 else rng.choice(ips)
        r = rng.random()
        status = rng.choice([500, 502, 503, 504]) if r < 0.06 + (0.10 if ip in hot else 0) else rng.choice([200, 200, 200, 201, 204, 301, 304, 400, 401, 404])
        size = rng.choice([500, 503, 1024, 2048, 5000, 512])  # decoy: sizes that look like 5xx
        path = rng.choice(paths)
        ts = f"24/Sep/2026:{(i // 3600) % 24:02d}:{(i // 60) % 60:02d}:{i % 60:02d} +0000"
        ua = rng.choice(["Mozilla/5.0", "curl/8.4", "python-requests/2.32 (status 500 test)"])
        lines.append(f'{ip} - - [{ts}] "GET {path} HTTP/1.1" {status} {size} "-" "{ua}"')
        if 500 <= status <= 599:
            c5[ip] += 1
    write(ws, "access.log", "\n".join(lines) + "\n")
    top = sorted(c5.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    return {"expected": [f"{ip} {n}" for ip, n in top]}


LOG_INSTR = """`access.log` is an HTTP access log in Combined Log Format. Find the 5 client IP addresses with the most responses whose status code is 5xx (500–599). Write them to `top5xx.txt`, one per line as `<ip> <count>`, sorted by count descending, with ties broken by IP address in ascending string order."""


def log_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    c = Check()
    p = ws / "top5xx.txt"
    if not c.true(p.exists(), "top5xx.txt missing"):
        return c.result()
    got = [re.sub(r"\s+", " ", l.strip()) for l in p.read_text().strip().splitlines() if l.strip()]
    c.eq(got, ctx["expected"], "top5xx.txt")
    return c.result()


# ── File organisation ───────────────────────────────────────────────────────
MAPPING = {"images": ["jpg", "jpeg", "png", "gif", "svg"], "docs": ["pdf", "docx", "txt", "md"], "data": ["csv", "json", "xlsx"], "code": ["py", "js", "sh"]}


def org_setup(ws: Path) -> dict:
    rng = random.Random(8)
    exts = [e for v in MAPPING.values() for e in v] + ["zip", "bin"]
    names = set()
    while len(names) < 60:
        base = rng.choice(["report", "photo", "notes", "data", "script", "draft", "scan", "export", "logo", "plan"]) + f"_{rng.randint(1, 999)}"
        e = rng.choice(exts)
        e = e.upper() if rng.random() < 0.15 else e
        names.add(f"{base}.{e}" if rng.random() > 0.05 else base)  # a few files without extension
    content = {}
    for n in sorted(names):
        data = f"file {n} {rng.random()}\n"
        write(ws, n, data)
        content[n] = hashlib.sha256(data.encode()).hexdigest()
    return {"files": content}


ORG_INSTR = """Organise the files in the workspace into sub-folders by extension (case-insensitive):
- `images/`: jpg, jpeg, png, gif, svg
- `docs/`: pdf, docx, txt, md
- `data/`: csv, json, xlsx
- `code/`: py, js, sh
- `other/`: anything else, including files without an extension
Keep file names unchanged. Then write `manifest.json` in the workspace root: a JSON object mapping every original file name to its new relative path (e.g. `"photo_1.JPG": "images/photo_1.JPG"`). Afterwards only the five folders and `manifest.json` may remain in the workspace root."""


def org_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    c = Check()
    def folder(n: str) -> str:
        ext = n.rsplit(".", 1)[1].lower() if "." in n else ""
        return next((k for k, v in MAPPING.items() if ext in v), "other")
    try:
        man = load_json(ws / "manifest.json")
    except Exception as e:
        c.true(False, f"manifest.json unreadable: {e}")
        man = {}
    wrong = 0
    for n, h in ctx["files"].items():
        want = f"{folder(n)}/{n}"
        p = ws / want
        if not (p.exists() and hashlib.sha256(p.read_bytes()).hexdigest() == h):
            wrong += 1
        if man.get(n, "").lstrip("./") != want:
            wrong += 1
    c.eq(wrong, 0, "misplaced files or manifest entries")
    extra = sorted(p.name for p in ws.iterdir() if p.name not in {"images", "docs", "data", "code", "other", "manifest.json"} and not p.name.startswith("."))
    c.eq(extra, [], "unexpected entries left in workspace root")
    c.eq(len(man), len(ctx["files"]), "manifest entry count")
    return c.result()


# ── Packaging ───────────────────────────────────────────────────────────────
def tar_setup(ws: Path) -> dict:
    files = {
        "project/src/app.py": "print('app')\n",
        "project/src/util.py": "X = 1\n",
        "project/src/__pycache__/app.cpython-311.pyc": "junk",
        "project/src/pkg/__init__.py": "",
        "project/src/pkg/__pycache__/x.pyc": "junk",
        "project/logs/today.log": "log\n",
        "project/logs/keep.txt": "keep\n",
        "project/debug.log": "dbg\n",
        "project/catalog.txt": "not a log\n",
        "project/backlog.md": "# backlog\n",
        "project/data.log.gz": "compressed (kept: does not end with .log)",
        "project/README.md": "# Project\n",
    }
    for k, v in files.items():
        write(ws, k, v)
    keep = sorted(k for k in files if "__pycache__" not in k and not k.endswith(".log"))
    return {"keep": keep}


TAR_INSTR = """Create `release.tar.gz` (gzip-compressed tar) containing the `project/` directory — member paths inside the archive must start with `project/` — while excluding every `__pycache__` directory and every file whose name ends in `.log`. Then write the SHA-256 checksum of the archive to `release.sha256` in exactly the format `sha256sum` produces."""


def tar_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    c = Check()
    p = ws / "release.tar.gz"
    if not c.true(p.exists(), "release.tar.gz missing"):
        return c.result()
    try:
        with tarfile.open(p, "r:gz") as t:
            members = sorted(m.name.lstrip("./") for m in t.getmembers() if m.isfile())
    except Exception as e:
        c.true(False, f"not a valid tar.gz: {e}")
        return c.result()
    c.eq(members, ctx["keep"], "archive files")
    line = (ws / "release.sha256").read_text().strip() if (ws / "release.sha256").exists() else ""
    m = re.match(r"^([0-9a-f]{64}) [ *](\./)?release\.tar\.gz$", line)
    c.true(m is not None, f"release.sha256 not in sha256sum format: {line!r}")
    if m:
        c.eq(m.group(1), sha256(p), "checksum")
    return c.result()


# ── HTTP service ────────────────────────────────────────────────────────────
HTTP_INSTR = """Write `server.py`, an HTTP JSON API using only the Python standard library. It takes the port as its first command-line argument (`python3 server.py 8080`) and listens on 127.0.0.1:
- `GET /health` → status 200, body `{"status": "ok"}`
- `POST /sum` with a JSON body `{"numbers": [...]}` → status 200, body `{"sum": <sum of the numbers>}`
- `POST /sum` with invalid JSON or a missing/non-list `numbers` → status 400 with a JSON body containing an `error` key
- any other path → status 404
All responses must use `Content-Type: application/json`. Start it in the background, test every endpoint with curl, then stop it."""


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _req(url: str, method: str = "GET", body: bytes | None = None) -> tuple[int, str, dict]:
    r = urllib.request.Request(url, data=body, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, e.headers.get("Content-Type", ""), json.loads(raw or b"null")
        except json.JSONDecodeError:
            return e.code, e.headers.get("Content-Type", ""), {"_raw": raw[:100].decode(errors="replace")}


def http_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    c = Check()
    if not c.true((ws / "server.py").exists(), "server.py missing"):
        return c.result()
    port = _free_port()
    proc = subprocess.Popen(["python3", "server.py", str(port)], cwd=ws, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(50):
            try:
                _req(base + "/health")
                break
            except Exception:
                time.sleep(0.2)
        st, ct, body = _req(base + "/health")
        c.eq((st, body), (200, {"status": "ok"}), "GET /health")
        c.true("application/json" in ct, f"content-type on /health: {ct!r}")
        st, _, body = _req(base + "/sum", "POST", json.dumps({"numbers": [1, 2, 3.5, -1]}).encode())
        c.eq(st, 200, "POST /sum status")
        c.true(isinstance(body, dict) and abs(float(body.get("sum", 1e9)) - 5.5) < 1e-9, f"POST /sum body: {body}")
        st, _, body = _req(base + "/sum", "POST", b"{not json")
        c.true(st == 400 and isinstance(body, dict) and "error" in body, f"invalid JSON → {st} {body}")
        st, _, body = _req(base + "/sum", "POST", json.dumps({"numbers": "12"}).encode())
        c.eq(st, 400, "non-list numbers status")
        st, _, _ = _req(base + "/nope")
        c.eq(st, 404, "unknown path status")
    except Exception as e:
        c.true(False, f"server unreachable or crashed: {type(e).__name__}: {e}")
    finally:
        proc.kill()
    return c.result()


# ── Git workflow ────────────────────────────────────────────────────────────
def git_setup(ws: Path) -> None:
    write(ws, "app.py", 'def main():\n    print("hello")\n\n\nif __name__ == "__main__":\n    main()\n')
    run("git init -q -b main && git config user.email dev@example.com && git config user.name Dev && git add app.py && git commit -qm 'Initial commit'", ws)
    write(ws, "README.md", "# App\n")
    run("git add README.md && git commit -qm 'Add README'", ws)


GIT_INSTR = """This workspace is a git repository on branch `main`. Create a branch named `feature/greet`, and on it add a function `greet(name)` to `app.py` that returns the string `Hello, <name>!` (e.g. `greet("Ada")` returns `Hello, Ada!`). Commit it with the message `Add greet function`. Then merge `feature/greet` into `main` with a merge commit (no fast-forward), and tag the resulting `main` commit as `v1.1.0`. Leave the repository on `main` with a clean working tree."""


def git_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    c = Check()
    g = lambda cmd: run(f"git {cmd}", ws)[1].strip()
    c.eq(g("branch --show-current"), "main", "current branch")
    c.true("feature/greet" in g("branch --list feature/greet"), "branch feature/greet missing")
    c.true("Add greet function" in g("log main --format=%s"), "commit 'Add greet function' not on main")
    merges = g("log main --merges --format=%H")
    c.true(merges != "", "no merge commit on main")
    c.eq(g("rev-parse v1.1.0^{commit}"), g("rev-parse main"), "tag v1.1.0 points at main")
    c.eq(g("status --porcelain"), "", "working tree clean")
    rc, out, err = run(["python3", "-c", "from app import greet; print(greet('Ada'))"], ws)
    c.eq(out.strip(), "Hello, Ada!", "greet('Ada')")
    return c.result()


# ── Config audit (needle in haystack) ───────────────────────────────────────
def cfg_setup(ws: Path) -> dict:
    rng = random.Random(33)
    want = []
    for i in range(40):
        name = f"svc-{rng.choice(['auth', 'billing', 'search', 'media', 'ledger', 'notify', 'geo', 'cart'])}-{i:02d}"
        timeout, retries = rng.choice([5, 10, 20, 30, 31, 45, 60, 90]), rng.choice([0, 1, 2, 3, 5])
        db_timeout = rng.choice([5, 60, 120])
        comment = " ; seconds" if rng.random() < 0.3 else ""
        body = f"# config for {name}\n[service]\nname = {name}\ntimeout = {timeout}{comment}\nretries = {retries}\n\n[database]\ntimeout = {db_timeout}\nretries = 9\n"
        if rng.random() < 0.2:
            body = body.replace("[service]", "[logging]\nlevel = info\n\n[service]")
        write(ws, f"configs/{rng.choice(['app', 'svc', 'conf'])}_{i:02d}.ini", body)
        if timeout > 30 and retries < 2:
            want.append(name)
    return {"expected": sorted(want)}


CFG_INSTR = """The `configs/` directory contains one INI file per service. Using the values in each file's `[service]` section only, list every service whose `timeout` is greater than 30 and whose `retries` is less than 2. Write the service names — the `name` value from the `[service]` section, not the file name — to `audit.txt`, one per line, sorted alphabetically."""


def cfg_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    c = Check()
    p = ws / "audit.txt"
    got = [l.strip() for l in p.read_text().splitlines() if l.strip()] if p.exists() else None
    c.eq(got, ctx["expected"], "audit.txt")
    return c.result()


TASKS = [
    EvalTask("ops-log-forensics", "ops", LOG_INSTR, log_setup, log_verify, max_turns=20),
    EvalTask("ops-organize-files", "ops", ORG_INSTR, org_setup, org_verify, max_turns=25),
    EvalTask("ops-archive-checksum", "ops", TAR_INSTR, tar_setup, tar_verify, max_turns=20),
    EvalTask("ops-http-service", "web", HTTP_INSTR, lambda ws: None, http_verify, max_turns=30, difficulty="hard"),
    EvalTask("git-feature-flow", "git", GIT_INSTR, git_setup, git_verify, max_turns=25),
    EvalTask("ops-config-audit", "ops", CFG_INSTR, cfg_setup, cfg_verify, max_turns=20),
]
