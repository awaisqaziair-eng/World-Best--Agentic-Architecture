"""Deterministic model clients: scripted, recording and replaying.

* ``ScriptedClient``  — returns a pre-programmed sequence (unit tests).
* ``RecordingClient`` — wraps a live client and appends every exchange to a
  cassette (JSONL).
* ``ReplayClient``    — serves a cassette back; in strict mode it verifies each
  request fingerprint and raises ``ReplayDivergence`` on the first mismatch,
  which pinpoints exactly where a harness change altered behaviour.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable, Iterable

from .. import jsonutil
from ..types import ModelResponse, ToolCall, Usage
from .base import ChatRequest, ModelError


def make_response(
    content: str | None = None,
    tool_calls: Iterable[tuple[str, dict[str, Any]] | ToolCall] = (),
    *,
    model: str = "scripted",
    input_tokens: int = 100,
    output_tokens: int = 20,
    finish_reason: str | None = None,
) -> ModelResponse:
    calls = []
    for i, tc in enumerate(tool_calls):
        if isinstance(tc, ToolCall):
            calls.append(tc)
        else:
            name, args = tc
            calls.append(ToolCall(id=f"call_s{i}_{jsonutil.digest([name, args, content], 6)}", name=name, arguments=args))
    return ModelResponse(
        content=content,
        tool_calls=calls,
        usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens, requests=1),
        model=model,
        finish_reason=finish_reason or ("tool_calls" if calls else "stop"),
    )


Script = ModelResponse | Exception | Callable[[ChatRequest], ModelResponse]


class ScriptedClient:
    def __init__(self, script: Iterable[Script], *, model: str = "scripted", repeat_last: bool = False) -> None:
        self.model = model
        self.script = list(script)
        self.requests: list[ChatRequest] = []
        self.repeat_last = repeat_last
        self._lock = threading.Lock()

    def complete(self, request: ChatRequest) -> ModelResponse:
        with self._lock:
            self.requests.append(request)
            if not self.script:
                raise ModelError("script exhausted", kind="retry_exhausted", model=self.model)
            item = self.script[0] if (self.repeat_last and len(self.script) == 1) else self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item) and not isinstance(item, ModelResponse):
            return item(request)
        return item


class RecordingClient:
    def __init__(self, inner: Any, cassette: str | Path) -> None:
        self.inner = inner
        self.model = inner.model
        self.path = Path(cassette)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def complete(self, request: ChatRequest) -> ModelResponse:
        resp = self.inner.complete(request)
        with self._lock, open(self.path, "a", encoding="utf-8") as fh:
            fh.write(jsonutil.dumps({"fingerprint": request.fingerprint(), "purpose": request.purpose, "response": resp.to_dict()}) + "\n")
        return resp


class ReplayDivergence(AssertionError):
    pass


class ReplayClient:
    def __init__(self, source: str | Path | list[dict[str, Any]], *, strict: bool = True, model: str = "replay") -> None:
        if isinstance(source, (str, Path)):
            with open(source, encoding="utf-8") as fh:
                self.entries = [json.loads(l) for l in fh if l.strip()]
        else:
            self.entries = list(source)
        self.strict = strict
        self.model = model
        self.index = 0
        self._lock = threading.Lock()

    def complete(self, request: ChatRequest) -> ModelResponse:
        with self._lock:
            if self.index >= len(self.entries):
                raise ReplayDivergence(f"replay exhausted after {self.index} responses")
            entry = self.entries[self.index]
            self.index += 1
        if self.strict and entry.get("fingerprint") and entry["fingerprint"] != request.fingerprint():
            raise ReplayDivergence(
                f"request #{self.index} diverged: recorded {entry['fingerprint']} != live {request.fingerprint()}"
            )
        return ModelResponse.from_dict(entry["response"])
