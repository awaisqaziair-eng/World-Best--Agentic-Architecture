"""Evaluation framework: task definition, checking helpers.

Every task has a *hidden* deterministic verifier the agent never sees. Tasks
are built so that shallow approaches fail (decoys, dirty data, superseded
documents, tie-breaking rules, performance thresholds).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from polymath.types import RunResult

Verify = Callable[[Path, RunResult, dict[str, Any]], tuple[bool, str]]


@dataclass
class EvalTask:
    id: str
    category: str
    instruction: str
    setup: Callable[[Path], dict[str, Any] | None]
    verify: Verify
    max_turns: int = 40
    max_wall_s: float = 900.0
    difficulty: str = "medium"
    criteria: list[str] = field(default_factory=list)
    teardown: Callable[[dict[str, Any]], None] | None = None


class Check:
    """Accumulates failed expectations into one readable verdict."""

    def __init__(self) -> None:
        self.fails: list[str] = []
        self.passes = 0

    def true(self, cond: Any, what: str) -> bool:
        if cond:
            self.passes += 1
        else:
            self.fails.append(what)
        return bool(cond)

    def eq(self, got: Any, want: Any, what: str) -> bool:
        return self.true(got == want, f"{what}: expected {want!r}, got {got!r}")

    def close(self, got: Any, want: float, what: str, tol: float = 0.011) -> bool:
        try:
            ok = abs(float(got) - want) <= tol
        except (TypeError, ValueError):
            ok = False
        return self.true(ok, f"{what}: expected {want} ± {tol}, got {got!r}")

    def result(self) -> tuple[bool, str]:
        if self.fails:
            return False, "; ".join(self.fails)[:1500]
        return True, f"{self.passes} checks passed"


def write(ws: Path, rel: str, content: str | bytes) -> Path:
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        p.write_bytes(content)
    else:
        p.write_text(content, encoding="utf-8")
    return p


def run(cmd: list[str] | str, cwd: Path, timeout: float = 120, stdin: str | None = None) -> tuple[int, str, str]:
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, shell=isinstance(cmd, str), input=stdin, env=env)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"


def run_py(ws: Path, code: str, timeout: float = 120) -> tuple[int, str, str]:
    """Run hidden verification code (kept outside the workspace) with cwd=ws."""
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
        fh.write("import sys; sys.path.insert(0, '.')\n" + code)
        path = fh.name
    try:
        return run([sys.executable, path], ws, timeout)
    finally:
        os.unlink(path)


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def answer_text(res: RunResult) -> str:
    return (res.answer or "").lower()
