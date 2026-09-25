"""State ledger: facts the agent must never lose, derived mechanically, never by an LLM.

Why (docs/research/02 §2.5, H2): Context Compaction Theory (arXiv 2608.01326) shows that
*generating* a summary can be more budget-efficient than *selecting* state, but a generated
summary can be wrong in ways nobody can check. Selection is verbatim, so it cannot invent a
state. The ledger is the selection half of a hybrid. The narrative stays with the summariser,
and these facts are recomputed from what actually happened:

* **files changed this run**: path, content hash, turn, and the tool that changed it. Found by
  diffing workspace snapshots after every tool that can write, the terminal included, so
  ``sed -i`` and ``pip install`` count too, not just ``write_file``;
* **recent commands**: turn, exit code, first line;
* **latest status of each test command** (pytest, unittest, npm test, go test, …);
* **context loss**: how many results were evicted to handles.

Delivery copies the cache-safe placement of the harness's ``SystemReminders``: an ephemeral
``UserPromptPart`` appended to the request tail inside ``wrap_model_request``, which runs after
core has persisted the durable history. The ledger therefore never enters ``message_history`` and
never changes the cached prefix. It is emitted only once the request shows the context has LOST
information:
* eviction stubs from ``RecallableEviction``;
* the prebuilt ``ClearToolResults`` placeholder;
* a history shorter than last turn's, i.e. a summariser rewrote it.

Before that the full history is present and the ledger would be redundant tokens. Detection
reads the request itself, so it needs no coupling to other capabilities, and ``wrap_model_request``
runs after every ``before_model_request`` compaction tier has already edited the request.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelRequest, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.tools import RunContext

SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", "dist", "build", ".polymath", ".sh"})
WRITING_TOOLS = frozenset({"write_file", "edit_file", "bash", "shell", "execute", "run_command", "exec_command"})
TEST_COMMAND = re.compile(r"\b(pytest|py\.test|unittest|nose2|tox|npm (?:run )?test|yarn test|pnpm test|jest|vitest|go test|cargo test|make (?:test|check)|mvn test|gradle(?:w)? test|ctest|rspec|phpunit)\b")
EXIT_CODE = re.compile(r"\[exit code: (-?\d+)")
MAX_FILES = 20_000
LOSSY_MARKERS = ("[evicted to handle ", "[tool result cleared]")


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    """path → (size, mtime_ns) for every regular file under root, skipping caches and VCS dirs."""
    out: dict[str, tuple[int, int]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            p = os.path.join(dirpath, name)
            try:
                st = os.stat(p, follow_symlinks=False)
            except OSError:
                continue
            out[os.path.relpath(p, root)] = (st.st_size, st.st_mtime_ns)
            if len(out) >= MAX_FILES:
                return out
    return out


def file_hash(path: Path) -> str:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()[:10]
    except OSError:
        return "deleted"


@dataclass
class FileFact:
    path: str
    sha: str
    turn: int
    by: str


@dataclass
class CommandFact:
    turn: int
    exit_code: int | None
    command: str


@dataclass
class StateLedger(AbstractCapability[Any]):
    """Observe tool executions; render verifiable state once context has become lossy."""

    workspace: Path = field(default_factory=Path.cwd)
    max_commands: int = 8
    max_files: int = 40
    renders: list[str] = field(default_factory=list, repr=False)  # every injected ledger, for evals

    files: dict[str, FileFact] = field(default_factory=dict, init=False)
    commands: list[CommandFact] = field(default_factory=list, init=False)
    tests: dict[str, CommandFact] = field(default_factory=dict, init=False)
    _snap: dict[str, tuple[int, int]] | None = field(default=None, init=False, repr=False)
    _turn: int = field(default=0, init=False, repr=False)
    _last_len: int = field(default=0, init=False, repr=False)
    _lossy: bool = field(default=False, init=False, repr=False)

    async def for_run(self, ctx: RunContext[Any]) -> StateLedger:
        run = replace(self)  # fresh per-run state; `renders` stays shared with the configured instance
        run._snap = snapshot(Path(self.workspace))
        return run

    async def wrap_model_request(self, ctx: RunContext[Any], *, request_context: Any, handler: Any) -> Any:
        self._turn += 1
        messages = request_context.messages
        if len(messages) < self._last_len or _has_lossy_marker(messages):
            self._lossy = True  # once information is gone it stays gone for the rest of the run
        self._last_len = len(messages)
        # Same guard as SystemReminders: only a real ModelRequest tail may carry an extra part.
        if self._lossy and messages and isinstance(last := messages[-1], ModelRequest):
            text = self.render()
            self.renders.append(text)
            messages[-1] = replace(last, parts=[*last.parts, UserPromptPart(content=text)])
        return await handler(request_context)

    async def after_tool_execute(self, ctx: RunContext[Any], *, call: ToolCallPart, tool_def: Any, args: Any, result: Any) -> Any:
        if call.tool_name not in WRITING_TOOLS:
            return result
        if call.tool_name in ("bash", "shell", "execute", "run_command", "exec_command"):
            cmd = str(call.args_as_dict().get("command") or call.args_as_dict().get("cmd") or "").strip()
            m = EXIT_CODE.search(str(result)[-400:])
            fact = CommandFact(self._turn, int(m.group(1)) if m else None, cmd.split("\n")[0][:140])
            self.commands = (self.commands + [fact])[-self.max_commands :]
            if TEST_COMMAND.search(cmd):
                self.tests[fact.command] = fact
        self._record_changes(call.tool_name)
        return result

    def _record_changes(self, by: str) -> None:
        root = Path(self.workspace)
        new = snapshot(root)
        old = self._snap or {}
        for path in set(new) | set(old):
            if new.get(path) != old.get(path):
                sha = file_hash(root / path) if path in new else "deleted"
                self.files[path] = FileFact(path, sha, self._turn, by)
        self._snap = new

    # ── rendering ───────────────────────────────────────────────────────────
    def render(self) -> str:
        lines = [f"<state-ledger turn={self._turn} · derived from tool executions, not from memory; trust it over recollection>"]
        if self.files:
            changed = sorted(self.files.values(), key=lambda f: f.turn)[-self.max_files :]
            lines.append("Files changed this run (path · sha256[:10] · turn · by):")
            lines += [f"  {f.path} · {f.sha} · t{f.turn} · {f.by}" for f in changed]
        if self.tests:
            lines.append("Latest result of each test command (turn · exit · command):")
            lines += [f"  t{t.turn} · {t.exit_code if t.exit_code is not None else '?'} · {t.command}" for t in self.tests.values()]
        if self.commands:
            lines.append("Last commands (turn · exit · command):")
            lines += [f"  t{c.turn} · {c.exit_code if c.exit_code is not None else '?'} · {c.command}" for c in self.commands]
        lines.append("Earlier tool results were evicted to handles; each stub names its handle and read_tool_result recovers it exactly.")
        lines.append("</state-ledger>")
        return "\n".join(lines)



def _has_lossy_marker(messages: list[Any]) -> bool:
    for msg in reversed(messages):
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if type(part) is ToolReturnPart and isinstance(part.content, str) and part.content.startswith(LOSSY_MARKERS):
                    return True
    return False
