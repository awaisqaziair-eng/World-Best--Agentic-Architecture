"""Gateway contracts: the request type, the client protocol and the error model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

from .. import jsonutil
from ..types import Message, ModelResponse


@dataclass
class ChatRequest:
    messages: list[Message]
    tools: list[dict[str, Any]] = field(default_factory=list)
    temperature: float | None = None
    max_tokens: int | None = None
    tool_choice: str | dict[str, Any] | None = None
    seed: int | None = None
    purpose: str = "agent"  # agent | compaction | judge | router | workflow
    # Streaming progress callback: (chunks_received, seconds_elapsed). Not part of the fingerprint.
    on_progress: Callable[[int, float], None] | None = field(default=None, repr=False, compare=False)

    def fingerprint(self) -> str:
        """Content hash used by record/replay to detect divergence."""
        return jsonutil.digest(
            {
                "messages": [
                    {k: v for k, v in m.to_dict().items() if k != "meta"} for m in self.messages
                ],
                "tools": sorted(t["function"]["name"] for t in self.tools),
                "tool_choice": self.tool_choice,
                "purpose": self.purpose,
            }
        )


@runtime_checkable
class ModelClient(Protocol):
    model: str

    def complete(self, request: ChatRequest) -> ModelResponse: ...


# ── Error model ─────────────────────────────────────────────────────────────
RETRYABLE = "retryable"              # transient: 429/5xx/timeouts (retried inside the client)
RETRY_EXHAUSTED = "retry_exhausted"  # transient errors outlived the retry budget
BAD_REQUEST = "bad_request"          # 400/422: the request itself is wrong
CONTEXT_OVERFLOW = "context_overflow"  # prompt exceeds the model window
AUTH = "auth"                        # 401/403
NOT_FOUND = "not_found"              # 404: model not deployed for this account
ALL_FAILED = "all_failed"            # every client in a fallback chain failed

# Kinds that say "this model/endpoint is unhealthy" and justify failing over.
FAILOVER_KINDS = frozenset({RETRY_EXHAUSTED, NOT_FOUND, AUTH, RETRYABLE})


class ModelError(Exception):
    def __init__(
        self,
        message: str,
        *,
        kind: str,
        model: str = "?",
        status: int | None = None,
        causes: list["ModelError"] | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.model = model
        self.status = status
        self.causes = causes or []

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": str(self),
            "kind": self.kind,
            "model": self.model,
            "status": self.status,
            "causes": [c.to_dict() for c in self.causes],
        }
