"""Deterministic workflows (journal/resume), chaos (fault injection) and crash durability."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from polymath import events as ev
from polymath.events import EventLog
from polymath.gateway.openai_compat import OpenAICompatClient
from polymath.kernel.runtime import Runtime
from polymath.kernel.workflow import Loop, Step, Workflow
from polymath.kernel.workflows_builtin import fix_until_green
from polymath.types import ModelResponse

from .helpers import FakeOpenAIServer, R, TempDirTest, bash, finish, no_sleep, wire_call

REPO = Path(__file__).resolve().parent.parent


class TestWorkflows(TempDirTest):
    def test_fix_until_green(self):
        (self.ws / "calc.py").write_text("def add(a, b):\n    return a - b\n")
        (self.ws / "check.py").write_text("from calc import add\nassert add(2, 3) == 5, 'add is wrong'\nprint('OK')\n")
        fixed = R(tool_calls=[("write_file", {"path": "calc.py", "content": "def add(a, b):\n    return a + b\n"})])
        rt, _ = self.runtime([fixed, finish("fixed add")])
        res = fix_until_green().run(rt, self.ws, {"cmd": "python3 check.py"})
        steps = list(res.outputs)
        self.assertEqual(steps, ["repair#1.test", "repair#1.fix", "repair#2.test"])
        self.assertEqual(res.outputs["repair#2.test"]["rc"], 0)

    def test_journal_skips_completed_steps_on_rerun(self):
        calls: list[str] = []

        def mk(name: str, fail: bool = False):
            def fn(c):
                calls.append(name)
                if fail and not (c.workspace / "ok").exists():
                    raise RuntimeError("crash in step")
                return {"step": name}

            return fn

        wf = Workflow("t", [Step("a", mk("a")), Step("b", mk("b")), Step("c", mk("c", fail=True))])
        rt, _ = self.runtime([])
        with self.assertRaises(RuntimeError):
            wf.run(rt, self.ws, {}, session_id="wf1")
        (self.ws / "ok").write_text("")
        res = wf.run(rt, self.ws, {}, session_id="wf1")  # resume
        self.assertEqual(calls, ["a", "b", "c", "c"])  # a and b ran exactly once
        self.assertEqual(res.skipped_from_journal, ["a", "b"])

    def test_loop_until_and_when(self):
        counter = {"n": 0}

        def inc(c):
            counter["n"] += 1
            return {"n": counter["n"]}

        wf = Workflow("l", [Loop("L", [Step("inc", inc), Step("never", lambda c: 1 / 0, when=lambda c: False)], until=lambda c: c.last("inc")["n"] >= 3, max_iterations=10)])
        rt, _ = self.runtime([])
        res = wf.run(rt, self.ws, {})
        self.assertEqual(list(res.outputs), ["L#1.inc", "L#2.inc", "L#3.inc"])


class TestChaos(TempDirTest):
    def _script(self):
        return [
            {"content": "", "tool_calls": [wire_call("bash", {"command": "echo chaos > c.txt"}, "a1")]},
            {"content": "", "tool_calls": [wire_call("bash", {"command": "cat c.txt"}, "a2")]},
            {"content": "", "tool_calls": [wire_call("finish", {"answer": "survived"}, "a3")]},
        ]

    def test_agent_survives_every_fault_kind(self):
        # 3 of every 4 requests fail, cycling through all six fault kinds deterministically.
        srv = FakeOpenAIServer(self._script(), fault_every=4)
        client = OpenAICompatClient("fake", base_url="http://fake/v1", api_key="k", transport=srv, sleep=no_sleep, max_retries=12)
        rt = Runtime(self.cfg(), client=client, utility=False, console=False)
        res = rt.run("chaos", self.ws)
        self.assertEqual((res.state, res.answer), ("completed", "survived"))
        self.assertEqual(len(srv.fault_log), 9)
        self.assertEqual(set(srv.fault_log), set(FakeOpenAIServer.KINDS))
        self.assertEqual((self.ws / "c.txt").read_text(), "chaos\n")

    def test_agent_survives_random_faults(self):
        for seed in range(5):
            ws = self.tmp / f"ws{seed}"
            ws.mkdir()
            srv = FakeOpenAIServer(self._script(), faults=0.4, seed=seed)
            client = OpenAICompatClient("fake", base_url="http://fake/v1", api_key="k", transport=srv, sleep=no_sleep, max_retries=15)
            rt = Runtime(self.cfg(home=str(self.tmp / f"h{seed}")), client=client, utility=False, console=False)
            self.assertEqual(rt.run("chaos", ws).answer, "survived", f"seed {seed}")
        # Every request on the wire had valid tool pairing.
        for body in srv.requests:
            open_ids: set[str] = set()
            for m in body["messages"]:
                if m["role"] == "assistant":
                    open_ids = {tc["id"] for tc in m.get("tool_calls") or []}
                elif m["role"] == "tool":
                    self.assertIn(m["tool_call_id"], open_ids)


CHILD = r"""
import sys, time
sys.path.insert(0, {repo!r})
from polymath.config import Config
from polymath.gateway.testing import ScriptedClient, make_response
from polymath.kernel.runtime import Runtime
cfg = Config().replace(home={home!r}, verbosity=0, enable_web=False, fallback_models=[])
client = ScriptedClient([make_response(tool_calls=[("bash", {{"command": "echo started >> log.txt; sleep 30; echo finished >> log.txt"}})])])
rt = Runtime(cfg, client=client, utility=False, console=False)
rt.run("long job", {ws!r}, session_id="killme")
"""


class TestCrashDurability(TempDirTest):
    def test_kill_dash_nine_then_resume(self):
        home = str(self.tmp / "home")
        code = CHILD.format(repo=str(REPO), home=home, ws=str(self.ws))
        proc = subprocess.Popen([sys.executable, "-c", code], start_new_session=True)
        events = Path(home) / "sessions" / "killme" / "events.jsonl"
        deadline = time.time() + 20
        while time.time() < deadline:
            if (self.ws / "log.txt").exists():
                break
            time.sleep(0.1)
        self.assertTrue((self.ws / "log.txt").exists(), "child never started the tool")
        os.killpg(proc.pid, signal.SIGKILL)  # kill -9 the agent AND its shell mid-tool
        proc.wait()
        types = [e.type for e in EventLog.load(events)]
        self.assertIn(ev.MODEL_RESPONSE, types)
        self.assertNotIn(ev.TOOL_RESULT, types)

        # Resume in a new process-equivalent runtime; the pending call re-executes (at-least-once).
        fast = R(tool_calls=[("finish", {"answer": "recovered"})])
        rt = Runtime(self.cfg(home=home), client=__import__("polymath.gateway.testing", fromlist=["ScriptedClient"]).ScriptedClient([fast]), utility=False, console=False)
        # Make the re-executed command fast by shadowing sleep in the workspace? No — at-least-once
        # semantics mean the real command runs again; keep the test quick by bounding the tool timeout.
        rt.cfg.shell_timeout_s = 2.0
        res = rt.resume("killme")
        self.assertEqual((res.state, res.answer), ("completed", "recovered"))
        types = [e.type for e in EventLog.load(events)]
        self.assertIn(ev.TOOL_RESULT, types)
        self.assertEqual((self.ws / "log.txt").read_text().count("started"), 2)  # executed twice: documented at-least-once
