"""Live console renderer: subscribes to the event log and prints progress."""

from __future__ import annotations

import sys
import threading
from typing import Any, TextIO

from .. import events as ev
from ..events import Event

_COLORS = {"dim": "\033[2m", "bold": "\033[1m", "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m", "blue": "\033[34m", "magenta": "\033[35m", "cyan": "\033[36m", "reset": "\033[0m"}


def summarize_args(name: str, args: dict[str, Any]) -> str:
    if name == "bash":
        cmd = str(args.get("command", ""))
        first = cmd.strip().split("\n")[0]
        more = f"  (+{cmd.count(chr(10))} lines)" if "\n" in cmd.strip() else ""
        return first[:160] + more
    if name in ("write_file",):
        return f"{args.get('path')} ({len(str(args.get('content', ''))):,} chars)"
    if name in ("edit_file", "read_file"):
        return str(args.get("path"))
    if name == "delegate":
        return f"{len(args.get('tasks') or [])} sub-agent(s)"
    if name == "finish":
        return str(args.get("answer", ""))[:120].replace("\n", " ")
    if name == "todo":
        items = args.get("items") or []
        return f"{sum(1 for i in items if i.get('status') == 'completed')}/{len(items)} done"
    s = ", ".join(f"{k}={str(v)[:60]!r}" for k, v in args.items())
    return s[:200]


class ConsoleRenderer:
    def __init__(self, stream: TextIO | None = None, *, verbosity: int = 1, color: bool | None = None) -> None:
        self.stream = stream or sys.stderr
        self.verbosity = verbosity
        self.color = self.stream.isatty() if color is None else color
        self._lock = threading.Lock()

    def c(self, text: str, color: str) -> str:
        return f"{_COLORS[color]}{text}{_COLORS['reset']}" if self.color else text

    def __call__(self, e: Event) -> None:
        if self.verbosity <= 0:
            return
        lines = self.render(e)
        if lines:
            with self._lock:
                self._clear_progress()
                for line in lines:
                    self.stream.write(line + "\n")
                self.stream.flush()

    # ── streaming heartbeat ─────────────────────────────────────────────
    _progress_shown = False
    _last_plain_progress = 0.0

    def progress(self, agent: str, chunks: int, seconds: float) -> None:
        """Show that a long generation is alive (TTY: one self-overwriting line; logs: every 30 s)."""
        if self.verbosity <= 0:
            return
        pad = "    " * agent.count(".")
        with self._lock:
            if self.stream.isatty():
                self.stream.write(f"\r{pad}  {self.c(f'… generating ({chunks:,} chunks, {seconds:.0f}s)', 'dim')}\033[K")
                self._progress_shown = True
            elif seconds - self._last_plain_progress >= 30:
                self._last_plain_progress = seconds
                self.stream.write(f"{pad}  … still generating ({chunks:,} chunks, {seconds:.0f}s)\n")
            else:
                return
            self.stream.flush()

    def _clear_progress(self) -> None:
        if self._progress_shown:
            self.stream.write("\r\033[K")
            self._progress_shown = False
        self._last_plain_progress = 0.0

    def render(self, e: Event) -> list[str]:
        depth = e.agent.count(".")
        pad = "    " * depth
        tag = self.c(f"[{e.agent}]", "dim") + " " if depth else ""
        d = e.data
        out: list[str] = []
        if e.type == ev.TASK_SUBMITTED:
            instr = (d.get("task") or {}).get("instruction", "")
            out.append(pad + tag + self.c("━━ task ", "bold") + instr.strip().split("\n")[0][:140])
            prof = d.get("profile")
            if prof and self.verbosity >= 2:
                out.append(pad + self.c(f"   profile: {prof.get('category')} / {prof.get('complexity')} skills={prof.get('skills')}", "dim"))
        elif e.type == ev.MODEL_RESPONSE:
            r = d["response"]
            if self.verbosity >= 2 and r.get("reasoning"):
                out.append(pad + tag + self.c("  ∴ " + r["reasoning"].strip().replace("\n", " ")[:300], "dim"))
            if r.get("content") and r["content"].strip():
                out.append(pad + tag + self.c("  " + r["content"].strip().replace("\n", " ")[:300], "cyan"))
            for tc in r.get("tool_calls") or []:
                out.append(pad + tag + self.c(f"  ▶ {tc['name']}", "blue") + " " + summarize_args(tc["name"], tc.get("arguments") or {}))
        elif e.type == ev.TOOL_RESULT:
            res = d["result"]
            if res["name"] in ("finish", "todo", "notes"):
                return out
            text = (res.get("output") or "").strip()
            lines = text.split("\n")
            footer = lines[-1] if res["name"] == "bash" and lines and lines[-1].startswith("[exit code") else ""
            first = lines[0][:150] if lines else ""
            mark = self.c("✗", "red") if res.get("is_error") else self.c("✓", "green")
            summary = first if not footer or first == footer else f"{first}  {self.c(footer, 'dim')}"
            out.append(pad + tag + f"    {mark} {summary}")
        elif e.type == ev.CONTEXT_CLEARED:
            out.append(pad + tag + self.c(f"  ⟲ cleared {d.get('cleared')} old tool outputs (~{d.get('est_saved_tokens', 0):,} tokens)", "magenta"))
        elif e.type == ev.CONTEXT_COMPACTED:
            out.append(pad + tag + self.c(f"  ⟲ compacted {d.get('summarized_entries')} entries ({d.get('method')}, was ~{d.get('before_tokens', 0):,} tokens)", "magenta"))
        elif e.type == ev.VERIFICATION:
            v = d["verification"]
            mark = self.c("✔ verification passed", "green") if v["passed"] else self.c("✘ verification failed", "red")
            out.append(pad + tag + f"  {mark} ({v['method']}) " + (v.get("detail") or "").strip().split("\n")[0][:160])
        elif e.type == ev.MODEL_ERROR:
            out.append(pad + tag + self.c(f"  ! model error [{d.get('kind')}] {str(d.get('message'))[:200]}", "red"))
        elif e.type == ev.HARNESS_NOTE:
            out.append(pad + tag + self.c(f"  · {d.get('kind')}: {str(d.get('detail', ''))[:200]}", "yellow"))
        elif e.type == ev.MESSAGE_USER and d.get("source") in ("harness", "verifier") and self.verbosity >= 2:
            out.append(pad + tag + self.c("  ↳ " + d.get("content", "").replace("\n", " ")[:200], "yellow"))
        elif e.type == ev.TASK_COMPLETED:
            r = d["result"]
            col = "green" if r["state"] == "completed" else "red"
            u = r.get("usage") or {}
            out.append(
                pad + tag + self.c(f"━━ {r['state']} ({r['stop_reason']}) · {r['turns']} turns · {r['tool_calls']} tool calls · "
                f"{u.get('total', 0):,} tokens · {r['duration_s']:.1f}s", col)
            )
        return out
