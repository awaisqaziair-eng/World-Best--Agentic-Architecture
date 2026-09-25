"""Persistent terminal — the agent's universal actuator.

Design (see docs/07-terminal-subsystem.md and ADR-001):

* **One long-lived bash per agent.** ``cd``, exported variables, activated
  virtualenvs and shell functions persist across calls, exactly like a human
  terminal session.
* **Commands are sourced from a script file** — ``source cmd_N.sh < /dev/null``.
  Heredocs, multi-line programs, unbalanced quotes and syntax errors are
  contained in that one ``source``; they can never desynchronise the shell.
  stdin is ``/dev/null`` so nothing can block waiting for input.
* **Completion is detected with a per-command random sentinel** printed after
  the command together with ``$?`` and ``$PWD``.
* **Timeouts escalate SIGINT → SIGTERM → SIGKILL, but only against processes
  the timed-out command created.** Background servers the agent launched
  earlier survive. If the shell itself is wedged it is replaced and the model
  is told exactly what state was lost.
* **Output hygiene:** ANSI stripped, ``\\r`` progress bars collapsed, capture
  capped (head + rolling tail) so a runaway ``yes`` cannot exhaust memory.
"""

from __future__ import annotations

import fnmatch
import os
import re
import select
import shlex
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

HEAD_CAP = 2 * 1024 * 1024
TAIL_CAP = 2 * 1024 * 1024
_ANSI = re.compile(rb"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[()][A-Za-z0-9]")


@dataclass
class ShellResult:
    output: str
    exit_code: int | None
    duration_s: float
    cwd: str
    timed_out: bool = False
    restarted: bool = False
    shell_died: bool = False
    dropped_bytes: int = 0


class _Capture:
    """Bounded byte capture: frozen head + rolling tail."""

    def __init__(self) -> None:
        self.data = bytearray()
        self.head: bytes | None = None
        self.dropped = 0

    def feed(self, chunk: bytes) -> None:
        self.data += chunk
        if self.head is None and len(self.data) > HEAD_CAP + 2 * TAIL_CAP:
            self.head = bytes(self.data[:HEAD_CAP])
            self.dropped += len(self.data) - HEAD_CAP - TAIL_CAP
            self.data = self.data[-TAIL_CAP:]
        elif self.head is not None and len(self.data) > 2 * TAIL_CAP:
            self.dropped += len(self.data) - TAIL_CAP
            self.data = self.data[-TAIL_CAP:]

    def find(self, needle: bytes) -> int:
        return self.data.find(needle, max(0, len(self.data) - 4 * TAIL_CAP))

    def text_until(self, idx: int | None) -> tuple[bytes, int]:
        body = bytes(self.data if idx is None else self.data[:idx])
        if self.head is not None:
            body = self.head + f"\n\n[... {self.dropped:,} bytes of output dropped ...]\n\n".encode() + body
        return body, self.dropped


def _children_map() -> dict[int, list[int]]:
    kids: dict[int, list[int]] = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/stat", "rb") as fh:
                stat = fh.read()
            # comm may contain spaces/parens; ppid is the 2nd field after the last ')'
            ppid = int(stat[stat.rindex(b")") + 2 :].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        kids.setdefault(ppid, []).append(int(d))
    return kids


def descendants(pid: int) -> set[int]:
    kids = _children_map()
    out: set[int] = set()
    stack = [pid]
    while stack:
        for c in kids.get(stack.pop(), []):
            if c not in out:
                out.add(c)
                stack.append(c)
    return out


def _clean(raw: bytes) -> str:
    raw = _ANSI.sub(b"", raw)
    text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n")
    if "\r" in text:
        text = "\n".join(line.rsplit("\r", 1)[-1] if "\r" in line else line for line in text.split("\n"))
    return text


def hermetic_env(strip: Iterable[str] = ()) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not any(fnmatch.fnmatch(k, pat) for pat in strip)}
    env.update(
        {
            "TERM": "dumb",
            "PAGER": "cat",
            "GIT_PAGER": "cat",
            "MANPAGER": "cat",
            "SYSTEMD_PAGER": "",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_EDITOR": "true",
            "EDITOR": "true",
            "VISUAL": "true",
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PIP_PROGRESS_BAR": "off",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "DEBIAN_FRONTEND": "noninteractive",
            "NO_COLOR": "1",
            "LANG": env.get("LANG") or "C.UTF-8",
            "LC_ALL": env.get("LC_ALL") or "C.UTF-8",
        }
    )
    env.pop("PROMPT_COMMAND", None)
    return env


class PersistentShell:
    def __init__(
        self,
        cwd: str | os.PathLike[str],
        *,
        shell: str = "/bin/bash",
        strip_env: Iterable[str] = (),
        scratch_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self.cwd = str(cwd)
        self.shell = shell
        self.strip_env = list(strip_env)
        self.scratch = Path(scratch_dir or Path(self.cwd) / ".polymath_shell")
        self.scratch.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._n = 0
        self.proc: subprocess.Popen[bytes] | None = None
        self.starts = 0
        self._start()

    # ── lifecycle ───────────────────────────────────────────────────────
    def _dispose_dead(self) -> None:
        """Release the pipes of a shell that already exited (no signals needed)."""
        if self.proc is None:
            return
        for stream in (self.proc.stdin, self.proc.stdout):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass
        try:
            self.proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        self.proc = None

    def _start(self) -> None:
        self._dispose_dead() if self.proc is not None and self.proc.poll() is not None else None
        cwd = self.cwd if os.path.isdir(self.cwd) else os.getcwd()
        self.proc = subprocess.Popen(
            [self.shell, "--noprofile", "--norc"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            env=hermetic_env(self.strip_env),
            start_new_session=True,
            bufsize=0,
        )
        assert self.proc.stdout is not None
        os.set_blocking(self.proc.stdout.fileno(), False)
        self.cwd = cwd
        self.starts += 1

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc else None

    def close(self) -> None:
        if self.proc is None:
            return
        try:
            for p in descendants(self.proc.pid):
                _signal(p, signal.SIGKILL)
            os.killpg(self.proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        for stream in (self.proc.stdin, self.proc.stdout):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass
        self.proc = None

    def restart(self) -> None:
        self.close()
        self._start()

    # ── execution ───────────────────────────────────────────────────────
    def run(self, command: str, timeout: float = 180.0) -> ShellResult:
        with self._lock:
            restarted = False
            if not self.alive:
                self._start()
                restarted = True
            assert self.proc is not None and self.proc.stdin is not None and self.proc.stdout is not None
            self._n += 1
            nonce = uuid.uuid4().hex
            marker = f"__POLYMATH_DONE_{nonce}__"
            script = self.scratch / f"cmd_{self._n:05d}_{nonce[:8]}.sh"  # unique even if a scratch dir is shared
            script.write_text(command if command.endswith("\n") else command + "\n", encoding="utf-8")
            line = (
                f"source {shlex.quote(str(script))} < /dev/null 2>&1; "
                f"__pm_rc=$?; printf '\\n%s %s %s\\n' '{marker}' \"$__pm_rc\" \"$PWD\"\n"
            )
            before = descendants(self.proc.pid)
            start = time.monotonic()
            try:
                self.proc.stdin.write(line.encode("utf-8"))
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError):
                self._start()
                return ShellResult("[shell was not running; a fresh shell was started — please re-run the command]", None, 0.0, self.cwd, restarted=True)

            cap = _Capture()
            fd = self.proc.stdout.fileno()
            mb = marker.encode()
            deadline = start + timeout
            timed_out = False
            escalation = [(signal.SIGINT, 3.0), (signal.SIGTERM, 2.0), (signal.SIGKILL, 2.0)]
            while True:
                now = time.monotonic()
                if now >= deadline:
                    if not escalation:
                        # Shell itself is stuck (e.g. a builtin busy-loop). Replace it.
                        body, dropped = cap.text_until(None)
                        self.restart()  # _start() reopens in the last known cwd
                        return ShellResult(
                            _clean(body),
                            None,
                            time.monotonic() - start,
                            self.cwd,
                            timed_out=True,
                            restarted=True,
                            dropped_bytes=dropped,
                        )
                    timed_out = True
                    sig, grace = escalation.pop(0)
                    targets = descendants(self.proc.pid) - before
                    for p in targets:
                        _signal(p, sig)
                    if not targets:
                        # Nothing to signal: the shell itself is busy (a builtin loop). Waiting out the
                        # full grace cannot help; keep only a short window for a marker already in flight.
                        grace = min(grace, 0.5)
                    deadline = time.monotonic() + grace
                    continue
                ready, _, _ = select.select([fd], [], [], min(0.25, max(0.0, deadline - now)))
                if ready:
                    try:
                        chunk = os.read(fd, 1 << 16)
                    except BlockingIOError:
                        continue
                    if not chunk:  # EOF: the shell exited (e.g. `exit 3`)
                        rc = self.proc.wait(timeout=5)
                        body, dropped = cap.text_until(None)
                        self._start()  # reopens in the last known cwd
                        return ShellResult(_clean(body), rc, time.monotonic() - start, self.cwd, shell_died=True, restarted=True, dropped_bytes=dropped)
                    cap.feed(chunk)
                    idx = cap.find(mb)
                    if idx >= 0:
                        nl = cap.data.find(b"\n", idx)
                        if nl < 0:
                            continue  # marker line not complete yet
                        tail = cap.data[idx + len(mb) : nl].decode("utf-8", "replace").strip()
                        rc_s, _, pwd = tail.partition(" ")
                        body, dropped = cap.text_until(idx)
                        if body.endswith(b"\n"):
                            body = body[:-1]  # the newline printf added before the marker
                        if pwd:
                            self.cwd = pwd
                        try:
                            rc = int(rc_s)
                        except ValueError:
                            rc = None
                        try:
                            script.unlink()
                        except OSError:
                            pass
                        return ShellResult(_clean(body), rc, time.monotonic() - start, self.cwd, timed_out=timed_out, restarted=restarted, dropped_bytes=dropped)
                elif self.proc.poll() is not None:
                    continue  # loop back; os.read will report EOF


def _signal(pid: int, sig: int) -> None:
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


# ── Tool wrapper ─────────────────────────────────────────────────────────────
from .base import Tool, ToolContext, ToolOutput  # noqa: E402  (avoid import cycle at module top)


class BashTool(Tool):
    name = "bash"
    description = """\
Run a command in a persistent bash shell: cwd, exported variables and virtualenvs persist between calls;
stdout+stderr are combined; stdin is /dev/null, so use non-interactive flags (-y, -m "msg").
Start servers/watchers in the background with output redirected (`nohup CMD > log 2>&1 &`), then poll.
Timeout 180 s by default (`timeout` up to 1800). Output over ~24k chars is clipped (head and tail kept,
full text saved to a file). Inspect big files with grep/head/wc instead of printing them."""
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "integer", "minimum": 1},
            "restart": {"type": "boolean", "default": False},
        },
        "required": ["command"],
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolOutput:
        sh = ctx.shell()
        if args.get("restart"):
            sh.restart()
        timeout = min(float(args.get("timeout") or ctx.config.shell_timeout_s), ctx.config.shell_max_timeout_s)
        res = sh.run(args["command"], timeout=timeout)
        out = res.output if res.output.strip() else "(no output)"
        footer = []
        if res.timed_out:
            footer.append(
                f"[TIMEOUT: command exceeded {timeout:.0f}s and was interrupted. Partial output above. "
                "Run long jobs in the background (`nohup CMD > log 2>&1 &`) or pass a larger `timeout`.]"
            )
        if res.shell_died:
            footer.append(f"[The shell exited (code {res.exit_code}). A fresh shell was started in {res.cwd}; exported variables/functions were lost.]")
        elif res.restarted and res.timed_out:
            footer.append(f"[The shell was unresponsive and was restarted in {res.cwd}; shell state was lost.]")
        footer.append(f"[exit code: {res.exit_code if res.exit_code is not None else 'n/a'} | {res.duration_s:.2f}s | cwd: {res.cwd}]")
        return ToolOutput(
            out.rstrip("\n") + "\n" + "\n".join(footer),
            # A non-zero exit (failing tests, grep with no match) is a *result*, not a tool fault.
            is_error=bool(res.timed_out or res.shell_died),
            meta={"exit_code": res.exit_code, "timed_out": res.timed_out, "cwd": res.cwd, "restarted": res.restarted},
        )
