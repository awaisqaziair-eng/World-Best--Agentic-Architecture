"""Polymath v2 enhancements on the Pydantic AI harness, driven end-to-end by scripted models."""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

try:
    from pydantic_ai import Agent
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel
    from pydantic_ai_harness.tool_output_limits import LocalFileStore

    from polymath.v2.agent import V2Options, build_agent
    from polymath.v2.ledger import StateLedger
    from polymath.v2.recall import STUB_PREFIX, RecallableEviction
    from polymath.v2.terminal import PolymathTerminal
except ImportError:  # the stdlib-only v1 core runs without the v2 extras
    Agent = None  # type: ignore[assignment]


def scripted(steps):
    """A FunctionModel that plays `steps`: each is a callable(messages) -> ModelResponse part list."""
    state = {"i": 0}

    def fn(messages, info):
        i = state["i"]
        state["i"] += 1
        parts = steps[i](messages) if i < len(steps) else [TextPart("done")]
        return ModelResponse(parts=parts)

    async def stream(messages, info):
        for part in fn(messages, info).parts:
            if isinstance(part, TextPart):
                yield part.content
            else:
                yield {0: DeltaToolCall(name=part.tool_name, json_args=json.dumps(part.args), tool_call_id=part.tool_call_id)}

    return FunctionModel(fn, stream_function=stream)


def call(name, args, cid):
    return lambda messages: [ToolCallPart(name, args, tool_call_id=cid)]


def last_return(messages, cid=None) -> str:
    for msg in reversed(messages):
        if isinstance(msg, ModelRequest):
            for p in msg.parts:
                if isinstance(p, ToolReturnPart) and (cid is None or p.tool_call_id == cid):
                    return str(p.content)
    return ""


@unittest.skipIf(Agent is None, "v2 extras not installed")
class TerminalToolsetTest(unittest.TestCase):
    def test_state_persists_and_background_job_returns_handles(self):
        ws = Path(tempfile.mkdtemp())
        seen = {}

        def capture(key, then):
            def step(messages):
                seen[key] = last_return(messages)
                return then(messages)
            return step

        model = scripted([
            call("bash", {"command": "mkdir -p sub && cd sub && export X=41"}, "c1"),
            capture("c1", call("bash", {"command": "echo cwd=$(pwd) x=$((X+1))"}, "c2")),
            capture("c2", call("bash", {"command": "sleep 5; echo finished", "background": True}, "c3")),
            capture("c3", lambda m: [TextPart("ok")]),
        ])
        Agent(model, capabilities=[PolymathTerminal(ws)]).run_sync("go")
        self.assertIn(f"cwd={ws.resolve()}/sub x=42", seen["c2"])
        self.assertIn("[exit code: 0", seen["c2"])
        m = re.search(r"pid=(\d+) log=(\S+)", seen["c3"])
        self.assertIsNotNone(m, seen["c3"])
        self.assertTrue(m.group(2).endswith(".log"))


def _history(n_pairs: int, size: int) -> list:
    msgs: list = [ModelRequest(parts=[UserPromptPart("task")])]
    for i in range(n_pairs):
        msgs.append(ModelResponse(parts=[ToolCallPart("read_file", {"path": f"f{i}.txt"}, tool_call_id=f"c{i}")]))
        msgs.append(ModelRequest(parts=[ToolReturnPart("read_file", f"line0 of f{i}\n" + ("x" * size) + f"\nlast line of f{i}", tool_call_id=f"c{i}")]))
    return msgs


class _Ctx:
    run_id = "r1"


@unittest.skipIf(Agent is None, "v2 extras not installed")
class RecallableEvictionTest(unittest.TestCase):
    def setUp(self):
        self.store = LocalFileStore(base_dir=Path(tempfile.mkdtemp()))
        self.tier = RecallableEviction(context_window=10_000, trigger_fraction=0.5, target_fraction=0.3, keep_pairs=2, min_result_tokens=100, store=self.store)

    def compact(self, msgs):
        return asyncio.run(self.tier.compact(msgs, _Ctx()))

    def test_evicts_oldest_to_target_keeps_recent_and_pairing(self):
        msgs = _history(8, 2_000)  # ~8 × 500 tokens ≈ 4k tokens; target 3k
        out = self.compact(msgs)
        returns = [p for m in out if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, ToolReturnPart)]
        calls = [p for m in out if isinstance(m, ModelResponse) for p in m.parts if isinstance(p, ToolCallPart)]
        self.assertEqual(len(returns), 8)
        self.assertEqual([c.tool_call_id for c in calls], [r.tool_call_id for r in returns])  # pairing intact
        evicted = [r for r in returns if str(r.content).startswith(STUB_PREFIX)]
        self.assertGreaterEqual(len(evicted), 2)
        self.assertEqual(evicted[0].tool_call_id, "c0")  # oldest first
        self.assertFalse(any(str(r.content).startswith(STUB_PREFIX) for r in returns[-2:]))  # keep_pairs
        self.assertEqual(self.tier.stats.eviction_batches, 1)

    def test_stub_describes_and_store_holds_exact_text(self):
        msgs = _history(6, 2_000)
        original = str(msgs[2].parts[0].content)
        out = self.compact(msgs)
        stub = str(out[2].parts[0].content)
        self.assertIn("read_file", stub)
        self.assertIn("f0.txt", stub)
        self.assertIn("'line0 of f0'", stub)
        self.assertIn("'last line of f0'", stub)
        handle = re.search(r"handle '([^']+)'", stub).group(1)
        self.assertEqual(asyncio.run(self.store.read(handle)).decode(), original)

    def test_spilled_previews_are_not_evicted_again(self):
        msgs = _history(8, 2_000)
        msgs[2] = ModelRequest(parts=[ToolReturnPart("read_file", "[Tool output too large (9 KB); stored to handle 'h'. " + "p" * 3000, tool_call_id="c0")])
        out = self.compact(msgs)
        self.assertTrue(str(out[2].parts[0].content).startswith("[Tool output too large"))

    def test_small_results_and_already_evicted_are_left_alone(self):
        msgs = _history(6, 100)  # ~25-token results: evicting them would not pay for the stub
        self.assertEqual(self.compact(msgs), msgs)
        msgs = _history(8, 2_000)
        once = self.compact(msgs)
        n = self.tier.stats.evictions
        self.assertGreater(n, 0)
        stubs = [str(p.content) for m in once if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, ToolReturnPart) and str(p.content).startswith(STUB_PREFIX)]
        again = self.compact(once)
        still = [str(p.content) for m in again if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, ToolReturnPart) and str(p.content).startswith(STUB_PREFIX)]
        self.assertEqual(stubs, still[: len(stubs)])  # a stub is never re-evicted or rewritten

    def test_recall_is_a_fault_and_pins_the_recalled_copy(self):
        out = self.compact(_history(8, 2_000))
        handle = re.search(r"handle '([^']+)'", str(out[2].parts[0].content)).group(1)
        recall = ToolCallPart("read_tool_result", {"handle": handle}, tool_call_id="r1")
        asyncio.run(self.tier.after_tool_execute(_Ctx(), call=recall, tool_def=None, args={}, result="..."))
        self.assertEqual(self.tier.stats.recalls, 1)
        self.assertEqual(self.tier.stats.reacquisitions, 1)
        self.assertGreater(self.tier._pinned_until["r1"], self.tier._turn)

    def test_identical_reread_after_eviction_counts_as_reacquisition(self):
        self.compact(_history(8, 2_000))
        again = ToolCallPart("read_file", {"path": "f0.txt"}, tool_call_id="n1")
        asyncio.run(self.tier.after_tool_execute(_Ctx(), call=again, tool_def=None, args={}, result="..."))
        self.assertEqual(self.tier.stats.re_reads, 1)

    def test_control_arm_is_irreversible_but_otherwise_identical(self):
        ctrl = RecallableEviction(context_window=10_000, trigger_fraction=0.5, target_fraction=0.3, keep_pairs=2, min_result_tokens=100, store=self.store, addressable=False)
        out_c = asyncio.run(ctrl.compact(_history(8, 2_000), _Ctx()))
        out_a = self.compact(_history(8, 2_000))
        kinds = lambda out: ["stub" if str(p.content).startswith(STUB_PREFIX) else "clr" if str(p.content) == "[tool result cleared]" else "keep" for m in out if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, ToolReturnPart)]
        self.assertEqual([k != "keep" for k in kinds(out_c)], [k != "keep" for k in kinds(out_a)])  # same victims
        self.assertIn("clr", kinds(out_c))
        self.assertNotIn("stub", kinds(out_c))
        again = ToolCallPart("read_file", {"path": "f0.txt"}, tool_call_id="n1")
        asyncio.run(ctrl.after_tool_execute(_Ctx(), call=again, tool_def=None, args={}, result="..."))
        self.assertEqual(ctrl.stats.re_reads, 1)  # reacquisition is still measured in the control arm

    def test_thrashing_enlarges_the_working_set(self):
        before = self.tier.target_fraction
        for i in range(3):
            self.tier._fault("x", f"n{i}")
        self.assertEqual(self.tier.stats.thrash_events, 1)
        self.assertGreater(self.tier.target_fraction, before)
        self.assertLess(self.tier.target_fraction, self.tier.trigger_fraction)


@unittest.skipIf(Agent is None, "v2 extras not installed")
class V2AgentEndToEndTest(unittest.TestCase):
    """Real agent, scripted model: eviction happens, the model recalls exactly, the ledger appears."""

    def test_evict_recall_and_ledger(self):
        ws = Path(tempfile.mkdtemp())
        for i in range(6):
            (ws / f"data{i}.txt").write_text(f"SECRET-{i}\n" + ("filler line\n" * 800))
        seen: dict[str, str] = {}
        prompts: list[str] = []

        def reads(i):
            def step(messages):
                return [ToolCallPart("read_file", {"path": f"data{i}.txt"}, tool_call_id=f"r{i}")]
            return step

        def recall_first(messages):
            stub = last_return(messages, "r0")
            seen["stub"] = stub
            handle = re.search(r"handle '([^']+)'", stub)
            # read_file output starts with a "[data0.txt | N lines]" header, so the secret is on line 2
            return [ToolCallPart("read_tool_result", {"handle": handle.group(1) if handle else "?", "limit": 3}, tool_call_id="rc")]

        def write_and_test(messages):
            seen["recalled"] = last_return(messages, "rc")
            return [ToolCallPart("bash", {"command": "echo fixed > out.txt && python3 -m unittest discover -s . -p 'nothing*' 2>&1 | tail -1"}, tool_call_id="b1")]

        def finish(messages):
            for m in messages:
                if isinstance(m, ModelRequest):
                    prompts.extend(p.content for p in m.parts if isinstance(p, UserPromptPart) and isinstance(p.content, str))
            return [TextPart("done")]

        model = scripted([reads(i) for i in range(6)] + [recall_first, write_and_test, finish])
        agent, tel = build_agent("test-model", ws, opts=V2Options(context_window=12_000, store_dir=Path(tempfile.mkdtemp())), model_obj=model)
        result = agent.run_sync("read everything")

        self.assertTrue(seen["stub"].startswith(STUB_PREFIX), seen["stub"][:200])  # r0 was evicted
        self.assertIn("SECRET-0", seen["recalled"])  # …and recalled exactly
        stats = tel.recall_runs[-1]
        self.assertGreaterEqual(stats.evictions, 1)
        self.assertEqual(stats.recalls, 1)
        # Ledger: injected once context became lossy, recorded the file the shell wrote and the test command.
        self.assertTrue(tel.ledger_renders)
        ledger = tel.ledger_renders[-1]
        self.assertIn("out.txt", ledger)
        self.assertIn("unittest", ledger)
        self.assertTrue(any("<state-ledger" in p for p in prompts))
        # …and it never entered the durable history.
        persisted = [p for m in result.all_messages() if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, UserPromptPart)]
        self.assertFalse(any("<state-ledger" in str(p.content) for p in persisted))


@unittest.skipIf(Agent is None, "v2 extras not installed")
class StateLedgerTest(unittest.TestCase):
    def test_silent_until_lossy(self):
        ws = Path(tempfile.mkdtemp())
        led = StateLedger(workspace=ws)
        run = asyncio.run(led.for_run(None))
        self.assertFalse(run._lossy)
        (ws / "a.py").write_text("x = 1\n")
        bash = ToolCallPart("bash", {"command": "pytest -q"}, tool_call_id="b")
        asyncio.run(run.after_tool_execute(None, call=bash, tool_def=None, args={}, result="1 passed\n[exit code: 0 | 0.1s | cwd: /]"))
        self.assertIn("a.py", run.files)
        self.assertEqual(run.tests["pytest -q"].exit_code, 0)
        text = run.render()
        self.assertIn("a.py", text)
        self.assertIn("t0 · 0 · pytest -q", text)


if __name__ == "__main__":
    unittest.main()
