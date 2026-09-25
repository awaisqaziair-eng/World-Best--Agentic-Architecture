"""Polymath's persistent terminal as a native Pydantic AI toolset.

Why it replaces the prebuilt shells (docs/research/03-terminal-bakeoff.md): 14/14 adversarial
scenarios against 10/14 for the best prebuilt shell. The prebuilt shells lose the tail of long
output, lose or leak processes on timeout, or run commands in the wrong directory.

From the competitors it adopts **background jobs with handles** (the idea behind Pydantic AI
Coder's ``shell``). ``background=true`` starts the command inside the SAME persistent shell, so
the job inherits the current directory, the virtualenv and exported variables, which Coder's
detached processes do not. The call returns the PID and a log path at once.

One ``PersistentShell`` per agent run (``for_run``), closed when the run ends. Calls run on a
worker thread because the shell's I/O is blocking.
"""

from __future__ import annotations

import asyncio
import shlex
import tempfile
from pathlib import Path
from typing import Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import FunctionToolset

from ..tools.terminal import PersistentShell, render_shell_result

DESCRIPTION = """\
Run a command in a persistent bash shell. `cd`, exported variables, activated virtualenvs and shell
functions persist between calls. stdout and stderr are combined. stdin is /dev/null, so use
non-interactive flags (-y, -m "msg"). Output ends with `[exit code: N | seconds | cwd]`.
Timeout defaults to 180 s (max 1800): a timed-out command is interrupted and the shell stays usable,
with background jobs left running. Very long output keeps its head and tail.
For servers, watchers and long jobs pass background=true: the command starts in this same shell
(same cwd and env) and you get its PID and log file at once. Poll with `tail -n 50 LOG`, stop with `kill PID`."""


class TerminalToolset(FunctionToolset[Any]):
    def __init__(self, workspace: Path, *, default_timeout: float = 180.0, max_timeout: float = 1800.0) -> None:
        super().__init__()
        self.workspace = Path(workspace).resolve()
        self.default_timeout = default_timeout
        self.max_timeout = max_timeout
        self._shell: PersistentShell | None = None
        self._scratch: Path | None = None
        self._bg = 0
        self.add_function(self.bash, name="bash", description=DESCRIPTION)

    async def for_run(self, ctx: Any) -> TerminalToolset:
        # Fresh instance per run: concurrent runs must not share one shell's cwd and env.
        return TerminalToolset(self.workspace, default_timeout=self.default_timeout, max_timeout=self.max_timeout)

    def _sh(self) -> PersistentShell:
        if self._shell is None:
            self._scratch = Path(tempfile.mkdtemp(prefix="polymath-sh-"))
            self._shell = PersistentShell(self.workspace, scratch_dir=self._scratch)
        return self._shell

    async def bash(self, command: str, timeout: float | None = None, background: bool = False) -> str:
        """Run a shell command.

        Args:
            command: The bash command or script (multi-line and heredocs are fine).
            timeout: Seconds before the command is interrupted (default 180, max 1800). Ignored when background.
            background: Start the command as a background job in this shell and return PID + log path immediately.
        """
        sh = self._sh()
        if background:
            self._bg += 1
            assert self._scratch is not None
            log = self._scratch / f"bg_{self._bg}.log"
            q = shlex.quote(str(log))
            script = f"( {command}\n) > {q} 2>&1 < /dev/null &\necho \"[background job] pid=$! log={log}\""
            res = await asyncio.to_thread(sh.run, script, 15.0)
            text, _ = render_shell_result(res, 15.0)
            return f"{text}\nPoll: tail -n 50 {q} · Stop: kill <pid>"
        t = min(float(timeout or self.default_timeout), self.max_timeout)
        res = await asyncio.to_thread(sh.run, command, t)
        text, _ = render_shell_result(res, t)
        return text

    async def __aexit__(self, *args: Any) -> bool | None:
        if self._shell is not None:
            await asyncio.to_thread(self._shell.close)
            self._shell = None
        return await super().__aexit__(*args)


class PolymathTerminal(AbstractCapability[Any]):
    """Capability wrapper so the terminal composes like any prebuilt capability."""

    def __init__(self, workspace: str | Path, *, default_timeout: float = 180.0, max_timeout: float = 1800.0) -> None:
        super().__init__()
        self.workspace = Path(workspace)
        self.default_timeout = default_timeout
        self.max_timeout = max_timeout

    def get_toolset(self) -> TerminalToolset:
        return TerminalToolset(self.workspace, default_timeout=self.default_timeout, max_timeout=self.max_timeout)
