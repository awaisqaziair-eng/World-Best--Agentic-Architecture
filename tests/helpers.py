"""Shared test fixtures: isolated runtimes with scripted models, fake HTTP servers."""

from __future__ import annotations

import json
import random
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable

from polymath.config import Config
from polymath.gateway.testing import ScriptedClient, make_response
from polymath.kernel.runtime import Runtime


class TempDirTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pm_test_"))
        self.ws = self.tmp / "ws"
        self.ws.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cfg(self, **kw: Any) -> Config:
        base = dict(home=str(self.tmp / "home"), verbosity=0, enable_web=False, fallback_models=[], max_turns=30)
        base.update(kw)
        return Config().replace(**base)

    def runtime(self, script: list[Any], *, utility: list[Any] | None = None, **cfg_kw: Any) -> tuple[Runtime, ScriptedClient]:
        client = ScriptedClient(script)
        util = ScriptedClient(utility) if utility is not None else False
        return Runtime(self.cfg(**cfg_kw), client=client, utility=util, console=False), client


R = make_response  # short alias


def finish(answer: str = "done", artifacts: list[str] | None = None):
    return R(tool_calls=[("finish", {"answer": answer, "artifacts": artifacts or []})])


def bash(cmd: str):
    return R(tool_calls=[("bash", {"command": cmd})])


class FakeOpenAIServer:
    """In-process OpenAI-compatible endpoint (a Transport) with fault injection.

    ``script`` is a list of assistant messages (dicts in OpenAI wire format).
    ``faults`` is a probability; faulty responses are drawn deterministically
    from ``rng`` among 429/500/503/timeout/garbage/empty-choices.
    """

    KINDS = ["429", "500", "503", "timeout", "garbage", "nochoices"]

    def __init__(self, script: list[dict[str, Any]], *, faults: float = 0.0, seed: int = 7, fault_every: int = 0) -> None:
        self.script = list(script)
        self.faults = faults
        self.fault_every = fault_every  # deterministic: every Nth request is a fault
        self.rng = random.Random(seed)
        self.requests: list[dict[str, Any]] = []
        self.fault_log: list[str] = []

    def __call__(self, url: str, headers: dict[str, str], body: bytes, timeout: float):
        from polymath.gateway.openai_compat import TransportError

        req = json.loads(body)
        self.requests.append(req)
        n = len(self.requests)
        scheduled = self.fault_every and n % self.fault_every != 0
        if scheduled or (self.faults and self.rng.random() < self.faults):
            kind = self.KINDS[len(self.fault_log) % len(self.KINDS)] if scheduled else self.rng.choice(self.KINDS)
            self.fault_log.append(kind)
            if kind == "timeout":
                raise TransportError("TimeoutError: The read operation timed out")
            if kind == "garbage":
                return 200, {}, b"<html>bad gateway</html>"
            if kind == "nochoices":
                return 200, {}, b'{"choices": []}'
            return int(kind), {"retry-after": "0"}, b'{"error": "transient"}'
        if not self.script:
            return 500, {}, b'{"error":"script exhausted"}'
        msg = self.script.pop(0)
        data = {
            "id": f"cmpl-{len(self.requests)}",
            "model": req["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", **msg}, "finish_reason": "tool_calls" if msg.get("tool_calls") else "stop"}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 50, "total_tokens": 1050},
        }
        return 200, {}, json.dumps(data).encode()


def wire_call(name: str, args: dict[str, Any], cid: str) -> dict[str, Any]:
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def no_sleep(_: float) -> None:
    return None


SleepRecorder = Callable[[float], None]
