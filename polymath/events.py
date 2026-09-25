"""Append-only, event-sourced session log.

Every observable fact about a run — the task, every model response, every tool
result, every context rewrite, every state change — is appended here *before*
the kernel acts on it. The conversation the model sees is a pure function of
this log (see ``polymath.context.conversation``), which gives three properties
for free:

* **Durability / resume** — a crashed or killed run is reconstructed by
  replaying the log and continuing from the last event (journaled execution).
* **Determinism** — recorded model responses can be replayed verbatim, turning a
  non-deterministic run into a deterministic regression test.
* **Observability** — the log *is* the audit trail and the trace source.

File format: JSON Lines, one event per line::

    {"seq": 12, "ts": 1727222400.123, "type": "tool.result", "agent": "main",
     "data": {...}}

``seq`` is dense, strictly increasing and assigned under a lock, so concurrent
sub-agents writing to the same session never interleave partial lines.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

from . import jsonutil
from .types import now

# ── Event type registry ─────────────────────────────────────────────────────
SESSION_STARTED = "session.started"          # {config, system_prompt, tools, model}
TASK_SUBMITTED = "task.submitted"            # {task}
TASK_STATE = "task.state"                    # {state, reason}
MESSAGE_USER = "message.user"                # {content, source: task|harness|user|verifier}
MODEL_REQUEST = "model.request"              # {model, n_messages, est_tokens, request_hash}
MODEL_RESPONSE = "model.response"            # {response: ModelResponse}
MODEL_ERROR = "model.error"                  # {model, error, kind}
TOOL_RESULT = "tool.result"                  # {result: ToolResult}
CONTEXT_CLEARED = "context.cleared"          # {upto_seq, cleared: n, saved_tokens}
CONTEXT_COMPACTED = "context.compacted"      # {upto_seq, summary, before_tokens, after_tokens}
PLAN_UPDATED = "plan.updated"                # {items}
VERIFICATION = "verification.result"         # {verification}
SUBAGENT_SPAWNED = "subagent.spawned"        # {child_agent, task}
SUBAGENT_FINISHED = "subagent.finished"      # {child_agent, result}
WORKFLOW_STEP_STARTED = "workflow.step.started"
WORKFLOW_STEP_COMPLETED = "workflow.step.completed"  # {step, output}
TASK_COMPLETED = "task.completed"            # {result: RunResult}
HARNESS_NOTE = "harness.note"                # {kind, detail} — internal diagnostics

ALL_TYPES = frozenset(
    v for k, v in dict(globals()).items() if k.isupper() and isinstance(v, str) and "." in v
)


@dataclass(frozen=True)
class Event:
    seq: int
    ts: float
    type: str
    agent: str
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "ts": self.ts, "type": self.type, "agent": self.agent, "data": self.data}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Event":
        return cls(seq=int(d["seq"]), ts=float(d["ts"]), type=d["type"], agent=d.get("agent", "main"), data=d.get("data") or {})


class EventLog:
    """Thread-safe append-only JSONL event store for one session."""

    def __init__(self, path: str | os.PathLike[str], *, fsync: bool = False) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fsync = fsync
        self._lock = threading.RLock()
        self._events: list[Event] = []
        self._listeners: list[Any] = []
        if self.path.exists():
            self._repair_torn_tail(self.path)
            self._events = list(self._read_file(self.path))
        self._fh = open(self.path, "a", encoding="utf-8")

    @staticmethod
    def _repair_torn_tail(path: Path) -> None:
        """Truncate a partially written final line left by a crash mid-append."""
        data = path.read_bytes()
        if not data or data.endswith(b"\n"):
            return
        cut = data.rfind(b"\n") + 1  # 0 if there is no complete line at all
        with open(path, "r+b") as fh:
            fh.truncate(cut)

    @property
    def session_id(self) -> str:
        return self.path.parent.name

    # ── writing ──────────────────────────────────────────────────────────
    def append(self, type_: str, data: dict[str, Any] | None = None, *, agent: str = "main") -> Event:
        with self._lock:
            seq = self._events[-1].seq + 1 if self._events else 1
            ev = Event(seq=seq, ts=round(now(), 6), type=type_, agent=agent, data=data or {})
            self._fh.write(jsonutil.dumps(ev.to_dict()) + "\n")
            self._fh.flush()
            if self._fsync:
                os.fsync(self._fh.fileno())
            self._events.append(ev)
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(ev)
            except Exception:  # listeners are best-effort observers
                pass
        return ev

    def subscribe(self, fn: Any) -> None:
        with self._lock:
            self._listeners.append(fn)

    def close(self) -> None:
        with self._lock:
            if not self._fh.closed:
                self._fh.close()

    # ── reading ──────────────────────────────────────────────────────────
    def events(self, *, agent: str | None = None, types: Iterable[str] | None = None) -> list[Event]:
        with self._lock:
            evs = list(self._events)
        if agent is not None:
            evs = [e for e in evs if e.agent == agent]
        if types is not None:
            ts = set(types)
            evs = [e for e in evs if e.type in ts]
        return evs

    def last(self, type_: str, *, agent: str | None = None) -> Event | None:
        for e in reversed(self.events(agent=agent)):
            if e.type == type_:
                return e
        return None

    @staticmethod
    def _read_file(path: Path) -> Iterator[Event]:
        with open(path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield Event.from_dict(json.loads(line))
                except (json.JSONDecodeError, KeyError):
                    # A torn final line (crash mid-write) is tolerated and dropped;
                    # a corrupt line elsewhere is a real error.
                    remaining = fh.read().strip()
                    if remaining:
                        raise ValueError(f"corrupt event log {path}:{lineno}")
                    return

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> list[Event]:
        return list(cls._read_file(Path(path)))

    def __len__(self) -> int:
        return len(self._events)

    def __enter__(self) -> "EventLog":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
