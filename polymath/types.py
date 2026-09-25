"""Core value types shared by every Polymath subsystem.

All types are plain dataclasses with explicit ``to_dict``/``from_dict`` so that
they round-trip losslessly through the append-only event log. The event log is
the single source of truth; these objects are projections of it.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

# ── Task lifecycle states (aligned with the A2A v1.0 task state machine) ──────
SUBMITTED = "submitted"
WORKING = "working"
INPUT_REQUIRED = "input_required"
COMPLETED = "completed"
FAILED = "failed"
CANCELED = "canceled"
TERMINAL_STATES = frozenset({COMPLETED, FAILED, CANCELED})

# ── Stop reasons (why the kernel loop ended) ─────────────────────────────────
STOP_FINISHED = "finished"            # agent called finish and verification passed/was skipped
STOP_TEXT_ANSWER = "text_answer"      # agent answered in prose without calling finish
STOP_BUDGET = "budget_exhausted"      # a budget (turns/tokens/time/tool calls) ran out
STOP_MODEL_ERROR = "model_error"      # every model in the fallback chain failed
STOP_NO_PROGRESS = "no_progress"      # repeated empty responses
STOP_CANCELED = "canceled"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now() -> float:
    return time.time()


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    raw_arguments: str | None = None
    parse_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ToolCall":
        return cls(
            id=d["id"],
            name=d["name"],
            arguments=d.get("arguments") or {},
            raw_arguments=d.get("raw_arguments"),
            parse_error=d.get("parse_error"),
        )


@dataclass
class Message:
    """Provider-neutral chat message. Wire formats are produced by protocols."""

    role: str  # system | user | assistant | tool
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None
    # Harness-private annotations (never sent to the model).
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = [tc.to_dict() for tc in self.tool_calls]
        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            d["name"] = self.name
        if self.meta:
            d["meta"] = self.meta
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Message":
        return cls(
            role=d["role"],
            content=d.get("content"),
            tool_calls=[ToolCall.from_dict(t) for t in d.get("tool_calls") or []],
            tool_call_id=d.get("tool_call_id"),
            name=d.get("name"),
            meta=dict(d.get("meta") or {}),
        )


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    requests: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cached_tokens += other.cached_tokens
        self.requests += other.requests

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "total": self.total}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "Usage":
        d = d or {}
        return cls(
            input_tokens=int(d.get("input_tokens", 0)),
            output_tokens=int(d.get("output_tokens", 0)),
            cached_tokens=int(d.get("cached_tokens", 0)),
            requests=int(d.get("requests", 0)),
        )


@dataclass
class ModelResponse:
    content: str | None
    tool_calls: list[ToolCall]
    usage: Usage
    model: str
    finish_reason: str | None = None
    reasoning: str | None = None
    latency_s: float = 0.0
    protocol: str = "native"
    attempts: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "tool_calls": [t.to_dict() for t in self.tool_calls],
            "usage": self.usage.to_dict(),
            "model": self.model,
            "finish_reason": self.finish_reason,
            "reasoning": self.reasoning,
            "latency_s": round(self.latency_s, 4),
            "protocol": self.protocol,
            "attempts": self.attempts,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ModelResponse":
        return cls(
            content=d.get("content"),
            tool_calls=[ToolCall.from_dict(t) for t in d.get("tool_calls") or []],
            usage=Usage.from_dict(d.get("usage")),
            model=d.get("model", "?"),
            finish_reason=d.get("finish_reason"),
            reasoning=d.get("reasoning"),
            latency_s=float(d.get("latency_s", 0.0)),
            protocol=d.get("protocol", "native"),
            attempts=int(d.get("attempts", 1)),
        )


@dataclass
class ToolResult:
    call_id: str
    name: str
    output: str
    is_error: bool = False
    duration_s: float = 0.0
    truncated: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ToolResult":
        return cls(
            call_id=d["call_id"],
            name=d["name"],
            output=d.get("output", ""),
            is_error=bool(d.get("is_error", False)),
            duration_s=float(d.get("duration_s", 0.0)),
            truncated=bool(d.get("truncated", False)),
            meta=dict(d.get("meta") or {}),
        )


@dataclass
class Budget:
    """Hard resource ceilings enforced by the kernel before every turn."""

    max_turns: int = 80
    max_tokens: int = 3_000_000
    max_wall_s: float = 3600.0
    max_tool_calls: int = 400

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "Budget":
        b = cls()
        for k, v in (d or {}).items():
            if hasattr(b, k):
                setattr(b, k, type(getattr(b, k))(v))
        return b

    def scaled(self, fraction: float) -> "Budget":
        return Budget(
            max_turns=max(4, int(self.max_turns * fraction)),
            max_tokens=max(50_000, int(self.max_tokens * fraction)),
            max_wall_s=max(60.0, self.max_wall_s * fraction),
            max_tool_calls=max(8, int(self.max_tool_calls * fraction)),
        )


@dataclass
class TaskSpec:
    instruction: str
    workspace: str
    id: str = field(default_factory=lambda: new_id("task"))
    acceptance_criteria: list[str] = field(default_factory=list)
    verify_command: str | None = None
    budget: Budget = field(default_factory=Budget)
    context: str | None = None  # extra context supplied by a caller (e.g. a parent agent)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["budget"] = self.budget.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TaskSpec":
        return cls(
            instruction=d["instruction"],
            workspace=d["workspace"],
            id=d.get("id") or new_id("task"),
            acceptance_criteria=list(d.get("acceptance_criteria") or []),
            verify_command=d.get("verify_command"),
            budget=Budget.from_dict(d.get("budget")),
            context=d.get("context"),
            metadata=dict(d.get("metadata") or {}),
        )


@dataclass
class Verification:
    passed: bool
    method: str  # command | judge | none
    detail: str = ""
    round: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunResult:
    session_id: str
    agent_id: str
    state: str
    stop_reason: str
    answer: str | None
    artifacts: list[str] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    turns: int = 0
    tool_calls: int = 0
    duration_s: float = 0.0
    verification: list[Verification] = field(default_factory=list)
    error: str | None = None
    models_used: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.state == COMPLETED

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "state": self.state,
            "stop_reason": self.stop_reason,
            "answer": self.answer,
            "artifacts": self.artifacts,
            "usage": self.usage.to_dict(),
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "duration_s": round(self.duration_s, 3),
            "verification": [v.to_dict() for v in self.verification],
            "error": self.error,
            "models_used": self.models_used,
        }
