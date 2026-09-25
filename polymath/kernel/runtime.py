"""Runtime: the composition root. Wires configuration into collaborators and
owns session storage. One Runtime can run many sessions concurrently."""

from __future__ import annotations

import os
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .. import events as ev
from ..config import Config, load_config
from ..context.tokens import Calibrator
from ..events import EventLog
from ..gateway import build_client
from ..gateway.base import ModelClient
from ..gateway.testing import ReplayClient
from ..memory import MemoryStore
from ..observability import ConsoleRenderer, Tracer
from ..tools.skills import Skill, discover_skills
from ..types import Budget, RunResult, TaskSpec, new_id
from .agent import Agent


class Runtime:
    def __init__(
        self,
        cfg: Config | None = None,
        *,
        client: ModelClient | None = None,
        utility: ModelClient | None | bool = None,
        console: bool | None = None,
    ) -> None:
        self.cfg = cfg or load_config()
        self.client: ModelClient = client or build_client(self.cfg)
        if utility is False:
            self.utility: ModelClient | None = None
        elif utility is None:
            # An injected main client (tests, replay) never doubles as the utility model:
            # that would silently consume its scripted/recorded responses.
            self.utility = build_client(self.cfg, self.cfg.utility_model, fallbacks=[self.cfg.model]) if client is None else None
        else:
            self.utility = utility  # type: ignore[assignment]
        self.skills: dict[str, Skill] = discover_skills(self.cfg.skill_dirs)
        self.memory: MemoryStore | None = MemoryStore(self.cfg.memory_file()) if self.cfg.enable_memory else None
        self.calibrator = Calibrator()
        self.console = ConsoleRenderer(verbosity=self.cfg.verbosity) if (console if console is not None else self.cfg.verbosity > 0) else None
        self._tracers: dict[str, Tracer] = {}
        self._lock = threading.Lock()

    # ── sessions ────────────────────────────────────────────────────────
    def new_session(self, session_id: str | None = None) -> EventLog:
        sid = session_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + new_id("s")[2:10]
        log = EventLog(self.cfg.sessions_path() / sid / "events.jsonl", fsync=self.cfg.fsync_events)
        if self.console:
            log.subscribe(self.console)
        return log

    def open_session(self, session_id: str) -> EventLog:
        path = self.cfg.sessions_path() / session_id / "events.jsonl"
        if not path.exists():
            raise FileNotFoundError(f"no session {session_id} under {self.cfg.sessions_path()}")
        log = EventLog(path, fsync=self.cfg.fsync_events)
        if self.console:
            log.subscribe(self.console)
        return log

    def list_sessions(self) -> list[dict[str, Any]]:
        root = self.cfg.sessions_path()
        out = []
        if not root.exists():
            return out
        for d in sorted(root.iterdir(), reverse=True):
            f = d / "events.jsonl"
            if not f.exists():
                continue
            evs = EventLog.load(f)
            task = next((e.data.get("task") for e in evs if e.type == ev.TASK_SUBMITTED and e.agent == "main"), None) or {}
            done = next((e.data.get("result") for e in reversed(evs) if e.type == ev.TASK_COMPLETED and e.agent == "main"), None)
            out.append(
                {
                    "session_id": d.name,
                    "task": (task.get("instruction") or "")[:100],
                    "state": done["state"] if done else "incomplete",
                    "events": len(evs),
                }
            )
        return out

    def tracer(self, log: EventLog) -> Tracer:
        with self._lock:
            sid = log.session_id
            if sid not in self._tracers:
                self._tracers[sid] = Tracer(log.path.parent / "trace.jsonl")
            return self._tracers[sid]

    # ── running ─────────────────────────────────────────────────────────
    def run(
        self,
        instruction: str,
        workspace: str | os.PathLike[str] = ".",
        *,
        acceptance_criteria: list[str] | None = None,
        verify_command: str | None = None,
        budget: Budget | None = None,
        mode: str = "full",
        session_id: str | None = None,
        ask_user: Callable[[str], str | None] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RunResult:
        b = budget or Budget(self.cfg.max_turns, self.cfg.max_tokens, self.cfg.max_wall_s, self.cfg.max_tool_calls)
        task = TaskSpec(
            instruction=instruction,
            workspace=str(Path(workspace).expanduser().resolve()),
            acceptance_criteria=list(acceptance_criteria or []),
            verify_command=verify_command,
            budget=b,
            metadata=dict(metadata or {}),
        )
        log = self.new_session(session_id)
        agent = Agent(self, log=log, workspace=task.workspace, mode=mode, ask_user=ask_user)
        try:
            return agent.run(task)
        finally:
            agent.close()
            log.close()

    def resume(self, session_id: str, *, budget: Budget | None = None) -> RunResult:
        """Continue an interrupted or failed session (see ``Agent.resume`` for the rules)."""
        log = self.open_session(session_id)
        try:
            return Agent.resume(self, log, budget=budget)
        finally:
            log.close()

    def replay(self, session_id: str, *, workspace: str | None = None, strict: bool = False) -> tuple[RunResult, EventLog]:
        """Re-run a recorded session against its recorded model responses.

        Tools are re-executed for real (in ``workspace``, default a fresh copy
        of nothing), so replay checks that the *harness* is deterministic given
        the same model outputs. Returns the new result and its log.
        """
        src = self.open_session(session_id)
        evs = src.events(agent="main")
        responses = [
            {"response": e.data["response"]} for e in evs if e.type == ev.MODEL_RESPONSE and not e.data.get("wrap_up")
        ]
        task_ev = next(e for e in evs if e.type == ev.TASK_SUBMITTED)
        task = TaskSpec.from_dict(task_ev.data["task"])
        if workspace:
            task.workspace = str(Path(workspace).resolve())
        src.close()
        replay_client = ReplayClient(responses, strict=strict, model=task_ev.data.get("model", "replay"))
        rt = Runtime(self.cfg.replace(enable_delegation=False), client=replay_client, utility=False, console=self.console is not None)
        log = rt.new_session(f"{session_id}.replay-{new_id('r')[2:8]}")
        agent = Agent(rt, log=log, workspace=task.workspace)
        agent.system.content = task_ev.data.get("system_prompt", agent.system.content)
        try:
            res = agent.run(task)
        finally:
            agent.close()
            log.close()
        return res, log

    def session_dir(self, session_id: str) -> Path:
        return self.cfg.sessions_path() / session_id

    def delete_session(self, session_id: str) -> None:
        shutil.rmtree(self.session_dir(session_id), ignore_errors=True)
