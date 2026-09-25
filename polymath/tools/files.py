"""File tools: read (paged, line-numbered), write, and exact-match edit.

``edit_file`` uses exact string replacement with a uniqueness requirement —
the most reliable edit format for LLMs (no line-number drift, no patch
context mismatch). When the match fails the tool returns the *closest* region
of the file so the model can correct itself in one step.
"""

from __future__ import annotations

import difflib
import os
from pathlib import Path
from typing import Any

from .base import Tool, ToolContext, ToolError, ToolOutput

MAX_LINE_CHARS = 2_000
DEFAULT_READ_LINES = 2_000


def _is_binary(sample: bytes) -> bool:
    if b"\x00" in sample:
        return True
    try:
        sample.decode("utf-8")
        return False
    except UnicodeDecodeError as e:
        # A multi-byte char cut at the sample boundary is not binary.
        return e.start < len(sample) - 4


def _numbered(lines: list[str], start: int) -> str:
    out = []
    for i, line in enumerate(lines, start):
        if len(line) > MAX_LINE_CHARS:
            line = line[:MAX_LINE_CHARS] + f"… [line truncated, {len(line):,} chars]"
        out.append(f"{i:>6}\t{line}")
    return "\n".join(out)


class ReadFileTool(Tool):
    name = "read_file"
    parallel_safe = True
    description = """\
Read a text file with line numbers (`<n>\t<text>`), up to 2000 lines from `offset`; page with offset/limit.
A directory path lists its entries; binary files are summarised. Never copy line-number prefixes into edit_file."""
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer", "minimum": 1},
            "limit": {"type": "integer", "minimum": 1},
        },
        "required": ["path"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        p = ctx.resolve(args["path"])
        if not p.exists():
            parent = p.parent
            hint = ""
            if parent.is_dir():
                names = sorted(os.listdir(parent))
                close = difflib.get_close_matches(p.name, names, n=3, cutoff=0.5)
                hint = f" Similar names in {parent}: {close}" if close else f" {parent} contains {len(names)} entries."
            raise ToolError(f"{p} does not exist.{hint}")
        if p.is_dir():
            entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name))
            lines = [f"{e.name}/" if e.is_dir() else f"{e.name}  ({e.stat().st_size:,} B)" for e in entries[:500]]
            more = f"\n… {len(entries) - 500} more" if len(entries) > 500 else ""
            return ToolOutput(f"Directory {p} ({len(entries)} entries):\n" + "\n".join(lines) + more)
        size = p.stat().st_size
        with open(p, "rb") as fh:
            sample = fh.read(8192)
        if _is_binary(sample):
            return ToolOutput(f"{p} is a binary file ({size:,} bytes). Use bash tools (file, xxd, python) to inspect it.")
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        offset = int(args.get("offset") or 1)
        limit = int(args.get("limit") or DEFAULT_READ_LINES)
        chunk = lines[offset - 1 : offset - 1 + limit]
        if not chunk:
            return ToolOutput(f"{p} has {len(lines)} lines; offset {offset} is past the end." if lines else f"{p} is empty.")
        body = _numbered(chunk, offset)
        end = offset + len(chunk) - 1
        if end < len(lines) or offset > 1:
            body += f"\n\n[showing lines {offset}-{end} of {len(lines)}; use offset/limit to read more]"
        ctx.state.setdefault("files_read", set()).add(str(p))
        return ToolOutput(body, meta={"lines": len(lines), "bytes": size})


class WriteFileTool(Tool):
    name = "write_file"
    description = """\
Create or overwrite a file (parent directories are created). Prefer edit_file for small changes."""
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        p = ctx.resolve(args["path"])
        if p.is_dir():
            raise ToolError(f"{p} is a directory")
        existed = p.exists()
        p.parent.mkdir(parents=True, exist_ok=True)
        content = args["content"]
        p.write_text(content, encoding="utf-8")
        n_lines = content.count("\n") + (0 if content.endswith("\n") or not content else 1)
        ctx.state.setdefault("files_written", set()).add(str(p))
        return ToolOutput(f"{'Overwrote' if existed else 'Created'} {p} ({len(content.encode()):,} bytes, {n_lines} lines).")


class EditFileTool(Tool):
    name = "edit_file"
    description = """\
Replace an exact, unique string in a file. Whitespace and indentation must match; include surrounding
lines to make it unique, or set replace_all. Use write_file to create files."""
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
            "replace_all": {"type": "boolean", "default": False},
        },
        "required": ["path", "old_string", "new_string"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        p = ctx.resolve(args["path"])
        if not p.is_file():
            raise ToolError(f"{p} does not exist (use write_file to create it)")
        old, new = args["old_string"], args["new_string"]
        if old == new:
            raise ToolError("old_string and new_string are identical — nothing to change")
        if old == "":
            raise ToolError("old_string is empty; use write_file to create or overwrite a file")
        text = p.read_text(encoding="utf-8", errors="replace")
        count = text.count(old)
        if count == 0:
            # No fuzzy application: a guessed edit is worse than a precise error.
            # Instead show the closest region so the model can retry exactly.
            raise ToolError(self._no_match_hint(text, old, p))
        if count > 1 and not args.get("replace_all"):
            lines = [text[: m].count("\n") + 1 for m in _find_all(text, old)]
            raise ToolError(
                f"old_string occurs {count} times in {p} (at lines {lines[:20]}). "
                "Add more surrounding context to make it unique, or set replace_all=true."
            )
        new_text = text.replace(old, new) if args.get("replace_all") else text.replace(old, new, 1)
        p.write_text(new_text, encoding="utf-8")
        first = new_text.find(new)
        line_no = new_text[:first].count("\n") + 1 if first >= 0 else 1
        all_lines = new_text.split("\n")
        lo = max(1, line_no - 3)
        hi = min(len(all_lines), line_no + new.count("\n") + 3)
        snippet = _numbered(all_lines[lo - 1 : hi], lo)
        ctx.state.setdefault("files_written", set()).add(str(p))
        return ToolOutput(f"Edited {p} ({count if args.get('replace_all') else 1} replacement(s)). Snippet:\n{snippet}")

    @staticmethod
    def _no_match_hint(text: str, old: str, p: Path) -> str:
        lines = text.split("\n")
        old_lines = old.strip("\n").split("\n")
        first = old_lines[0].strip()
        best_i, best_r = -1, 0.0
        if first:
            for i, line in enumerate(lines):
                r = difflib.SequenceMatcher(None, first, line.strip()).ratio()
                if r > best_r:
                    best_i, best_r = i, r
        msg = f"old_string not found in {p}."
        if best_i >= 0 and best_r >= 0.6:
            lo = max(0, best_i - 2)
            hi = min(len(lines), best_i + len(old_lines) + 2)
            msg += f" The most similar region (similarity {best_r:.0%}) is:\n" + _numbered(lines[lo:hi], lo + 1)
            msg += "\nCopy the text exactly (without line numbers) and retry."
        else:
            msg += " Re-read the file to get its exact current content."
        return msg


def _find_all(text: str, sub: str) -> list[int]:
    out, i = [], text.find(sub)
    while i >= 0:
        out.append(i)
        i = text.find(sub, i + max(1, len(sub)))
    return out
