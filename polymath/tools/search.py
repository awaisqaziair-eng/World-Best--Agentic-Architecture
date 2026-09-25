"""Search tools: glob (find files by name) and grep (search contents).

``grep`` uses ripgrep when present (fast, .gitignore-aware) and falls back to a
pure-Python scanner with identical output format, so behaviour is the same on
any host.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .base import Tool, ToolContext, ToolError, ToolOutput

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", ".tox", "dist", "build", ".polymath_shell"}
_RG = shutil.which("rg")


def _walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for f in sorted(filenames):
            yield Path(dirpath) / f


class GlobTool(Tool):
    name = "glob"
    parallel_safe = True
    description = """\
Find files by glob pattern (e.g. `**/*.py`); returns sorted relative paths. Skips .git, node_modules,
caches and virtualenvs."""
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string"},
        },
        "required": ["pattern"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        root = ctx.resolve(args.get("path") or ".")
        if not root.is_dir():
            raise ToolError(f"{root} is not a directory")
        pat = args["pattern"]
        if pat.startswith("/"):
            pat = pat.lstrip("/")
        matches: list[str] = []
        pat_nodir = pat[3:] if pat.startswith("**/") else None
        for f in _walk(root):
            rel = f.relative_to(root).as_posix()
            if fnmatch.fnmatch(rel, pat) or (pat_nodir and fnmatch.fnmatch(rel, pat_nodir)) or ("/" not in pat and fnmatch.fnmatch(f.name, pat)):
                matches.append(rel)
                if len(matches) >= 1000:
                    break
        if not matches:
            return ToolOutput(f"No files match {args['pattern']!r} under {root}")
        more = "\n[stopped at 1000 matches — narrow the pattern]" if len(matches) >= 1000 else ""
        return ToolOutput(f"{len(matches)} match(es) under {root}:\n" + "\n".join(matches) + more)


class GrepTool(Tool):
    name = "grep"
    parallel_safe = True
    description = """\
Regex search of file contents; returns `path:line:text`. Optional: glob filter, ignore_case, context
lines, max_results (default 200)."""
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string"},
            "glob": {"type": "string"},
            "ignore_case": {"type": "boolean", "default": False},
            "context": {"type": "integer", "minimum": 0, "maximum": 20, "default": 0},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 200},
        },
        "required": ["pattern"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        root = ctx.resolve(args.get("path") or ".")
        if not root.exists():
            raise ToolError(f"{root} does not exist")
        maxr = int(args.get("max_results") or 200)
        if _RG:
            return self._rg(args, root, maxr)
        return self._py(args, root, maxr)

    def _rg(self, args: dict[str, Any], root: Path, maxr: int) -> ToolOutput:
        cmd = [_RG, "--line-number", "--no-heading", "--color=never", "--max-columns=400", "--max-columns-preview"]
        if args.get("ignore_case"):
            cmd.append("-i")
        if args.get("context"):
            cmd += ["-C", str(int(args["context"]))]
        if args.get("glob"):
            cmd += ["--glob", args["glob"]]
        for d in sorted(SKIP_DIRS):
            cmd += ["--glob", f"!{d}"]
        cmd += ["--", args["pattern"], str(root)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            raise ToolError("search timed out after 60s; narrow the path or pattern")
        if proc.returncode == 2 and proc.stderr:
            raise ToolError(proc.stderr.strip()[:500])
        lines = proc.stdout.splitlines()
        return self._format(lines, root, maxr, args["pattern"])

    def _py(self, args: dict[str, Any], root: Path, maxr: int) -> ToolOutput:
        try:
            rx = re.compile(args["pattern"], re.I if args.get("ignore_case") else 0)
        except re.error as e:
            raise ToolError(f"invalid regex: {e}")
        files = [root] if root.is_file() else list(_walk(root))
        g = args.get("glob")
        ctxn = int(args.get("context") or 0)
        out: list[str] = []
        for f in files:
            if g and not (fnmatch.fnmatch(f.name, g) or fnmatch.fnmatch(str(f), g)):
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            lines = text.split("\n")
            for i, line in enumerate(lines):
                if rx.search(line):
                    lo, hi = max(0, i - ctxn), min(len(lines), i + ctxn + 1)
                    for j in range(lo, hi):
                        sep = ":" if j == i else "-"
                        out.append(f"{f}{sep}{j + 1}{sep}{lines[j][:400]}")
                    if len(out) > maxr * (1 + 2 * ctxn):
                        break
        return self._format(out, root, maxr, args["pattern"])

    @staticmethod
    def _format(lines: list[str], root: Path, maxr: int, pattern: str) -> ToolOutput:
        if not lines:
            return ToolOutput(f"No matches for {pattern!r} under {root}")
        prefix = str(root) + ("/" if root.is_dir() else "")
        shown = [l[len(prefix):] if root.is_dir() and l.startswith(prefix) else l for l in lines[:maxr]]
        more = f"\n[{len(lines) - maxr} more lines not shown — refine the pattern or raise max_results]" if len(lines) > maxr else ""
        header = f"Matches under {root}:\n" if root.is_dir() else ""
        return ToolOutput(header + "\n".join(shown) + more)
