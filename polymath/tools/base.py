"""Tool contract, execution context and registry.

A tool is a named, schema-described capability with a single entry point::

    run(args: dict, ctx: ToolContext) -> str | ToolOutput

The registry owns everything around that call — argument parsing and
validation, timing, exception capture, output budgeting and spill-to-disk —
so individual tools stay small and every tool behaves identically at the edges.
"""

from __future__ import annotations

import re
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from ..types import ToolCall, ToolResult
from .schema import SchemaError, validate

if TYPE_CHECKING:
    from ..config import Config
    from ..events import EventLog
    from .terminal import PersistentShell


@dataclass
class ToolOutput:
    text: str
    is_error: bool = False
    meta: dict[str, Any] = field(default_factory=dict)
    # Set by tools that already applied their own truncation (e.g. the terminal).
    truncated: bool = False


class ToolError(Exception):
    """Raise inside a tool for an expected, model-actionable failure."""


@dataclass
class ToolContext:
    workspace: Path
    session_dir: Path
    session_id: str
    agent_id: str
    config: "Config"
    events: "EventLog | None" = None
    depth: int = 0
    state: dict[str, Any] = field(default_factory=dict)
    # Late-bound collaborators injected by the kernel.
    spawn_subagents: Callable[..., Any] | None = None
    ask_user: Callable[[str], str | None] | None = None
    _shell: "PersistentShell | None" = None
    _lock: threading.RLock = field(default_factory=threading.RLock)

    @property
    def outputs_dir(self) -> Path:
        p = self.session_dir / "outputs"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def resolve(self, path: str) -> Path:
        p = Path(path).expanduser()
        return p if p.is_absolute() else (self.workspace / p)

    def shell(self) -> "PersistentShell":
        from .terminal import PersistentShell

        with self._lock:
            if self._shell is None or not self._shell.alive:
                self._shell = PersistentShell(
                    cwd=self.workspace,
                    shell=self.config.shell,
                    strip_env=self.config.hermetic_env_strip,
                    # Per-agent: parallel sub-agents must never share command scripts.
                    scratch_dir=self.session_dir / "shell" / self.agent_id,
                )
            return self._shell

    def close(self) -> None:
        with self._lock:
            if self._shell is not None:
                self._shell.close()
                self._shell = None


class Tool:
    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    parallel_safe: bool = False  # true ⇒ may run concurrently with other parallel-safe calls
    terminal: bool = False  # true ⇒ calling it can end the run (finish)

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str | ToolOutput:
        raise NotImplementedError

    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description.strip(), "parameters": self.parameters},
        }


_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")


def clip(text: str, limit: int, *, spill_path: Path | None = None) -> tuple[str, bool]:
    """Head+tail truncation; the elided middle is saved to ``spill_path``."""
    if len(text) <= limit:
        return text, False
    head = int(limit * 0.55)
    tail = limit - head
    omitted = len(text) - head - tail
    note = f"\n\n[... {omitted:,} characters omitted"
    if spill_path is not None:
        spill_path.write_text(text, encoding="utf-8")
        note += f"; full output saved to {spill_path} — use read_file/grep on it if you need the middle"
    note += " ...]\n\n"
    return text[:head] + note + text[-tail:], True


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for t in tools or []:
            self.register(t)

    def register(self, tool: Tool) -> None:
        if not tool.name:
            raise ValueError("tool without a name")
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def specs(self) -> list[dict[str, Any]]:
        """Deterministically ordered specs → stable prompt prefix → cache hits."""
        return [self._tools[n].spec() for n in self.names()]

    def subset(self, exclude: set[str]) -> "ToolRegistry":
        return ToolRegistry([t for n, t in sorted(self._tools.items()) if n not in exclude])

    # ── execution ───────────────────────────────────────────────────────
    def execute(self, call: ToolCall, ctx: ToolContext) -> ToolResult:
        start = time.monotonic()
        tool = self._tools.get(call.name)
        if tool is None:
            close = ", ".join(self.names())
            return ToolResult(call.id, call.name, f"Error: unknown tool '{call.name}'. Available tools: {close}", is_error=True)
        if call.parse_error:
            return ToolResult(
                call.id,
                call.name,
                f"Error: your arguments for '{call.name}' were not valid JSON ({call.parse_error}). "
                "Re-issue the call with a valid JSON object. For long file contents make sure quotes, "
                "backslashes and newlines inside strings are escaped.",
                is_error=True,
            )
        notes: list[str] = []
        try:
            args = validate(call.arguments, tool.parameters, notes=notes)
        except SchemaError as e:
            return ToolResult(
                call.id,
                call.name,
                "Error: invalid arguments — " + "; ".join(e.errors) + f"\nExpected schema: {tool.parameters}",
                is_error=True,
            )
        try:
            out = tool.run(args, ctx)
        except ToolError as e:
            out = ToolOutput(f"Error: {e}", is_error=True)
        except Exception as e:  # a tool bug must never kill the run
            tb = traceback.format_exc(limit=4)
            out = ToolOutput(f"Error: tool '{call.name}' crashed: {type(e).__name__}: {e}\n{tb}", is_error=True, meta={"crash": True})
        if isinstance(out, str):
            out = ToolOutput(out)
        text = _ANSI.sub("", out.text)
        truncated = out.truncated
        limit = ctx.config.max_tool_output_chars
        if len(text) > limit:
            text, truncated = clip(text, limit, spill_path=ctx.outputs_dir / f"{call.id}.txt")
        meta = dict(out.meta)
        if notes:
            meta["coercions"] = notes
        return ToolResult(
            call_id=call.id,
            name=call.name,
            output=text,
            is_error=out.is_error,
            duration_s=round(time.monotonic() - start, 4),
            truncated=truncated,
            meta=meta,
        )

    def execute_batch(self, calls: list[ToolCall], ctx: ToolContext, *, max_workers: int = 8) -> list[ToolResult]:
        """Run a turn's calls. Maximal runs of consecutive parallel-safe calls are
        executed concurrently; everything else runs in order. Results are always
        returned in call order, so the transcript is deterministic."""
        results: list[ToolResult | None] = [None] * len(calls)
        i = 0
        while i < len(calls):
            tool = self._tools.get(calls[i].name)
            if tool is not None and tool.parallel_safe:
                j = i
                while j < len(calls) and (t := self._tools.get(calls[j].name)) is not None and t.parallel_safe:
                    j += 1
                group = list(range(i, j))
                if len(group) == 1:
                    results[i] = self.execute(calls[i], ctx)
                else:
                    with ThreadPoolExecutor(max_workers=min(max_workers, len(group))) as ex:
                        for idx, res in zip(group, ex.map(lambda k: self.execute(calls[k], ctx), group)):
                            results[idx] = res
                i = j
            else:
                results[i] = self.execute(calls[i], ctx)
                i += 1
        return [r for r in results if r is not None]
