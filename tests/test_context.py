"""Conversation projection, clearing, compaction and emergency truncation."""

from __future__ import annotations

from polymath import events as ev
from polymath.context import ContextEngine, materialize, project
from polymath.events import EventLog
from polymath.gateway.testing import ScriptedClient, make_response
from polymath.types import Message, ModelResponse, ToolCall, ToolResult, Usage

from .helpers import TempDirTest

SYSTEM = Message("system", "sys")


def add_turn(log: EventLog, i: int, output: str, *, agent: str = "main") -> None:
    call = ToolCall(f"c{i}", "bash", {"command": f"cmd {i}"})
    resp = ModelResponse(content=f"step {i}", tool_calls=[call], usage=Usage(10, 2, requests=1), model="m")
    log.append(ev.MODEL_RESPONSE, {"response": resp.to_dict()}, agent=agent)
    log.append(ev.TOOL_RESULT, {"result": ToolResult(call.id, "bash", output).to_dict()}, agent=agent)


def assert_wire_valid(tc, msgs: list[Message]) -> None:
    """Every tool message must answer a call from the immediately preceding assistant turn."""
    open_ids: set[str] = set()
    for m in msgs:
        if m.role == "assistant":
            tc.assertEqual(open_ids, set(), "previous calls left unanswered")
            open_ids = {c.id for c in m.tool_calls}
        elif m.role == "tool":
            tc.assertIn(m.tool_call_id, open_ids)
            open_ids.discard(m.tool_call_id)
    tc.assertEqual(open_ids, set())


class TestContext(TempDirTest):
    def new_log(self, n_turns: int, size: int = 3000) -> EventLog:
        log = EventLog(self.tmp / "s" / "events.jsonl")
        self.addCleanup(log.close)
        log.append(ev.TASK_SUBMITTED, {"task": {"instruction": "do it", "workspace": str(self.ws)}})
        log.append(ev.MESSAGE_USER, {"content": "TASK: do it", "source": "task"})
        for i in range(n_turns):
            add_turn(log, i, f"out{i} " + "x" * size)
        return log

    def test_projection_counts(self):
        log = self.new_log(5)
        st = project(log.events(), "main")
        self.assertEqual((st.turns, st.tool_calls, st.usage.input_tokens), (5, 5, 50))
        msgs = materialize(st)
        self.assertEqual(msgs[0].content, "TASK: do it")
        assert_wire_valid(self, msgs)

    def test_pending_calls_and_synthetic_results(self):
        log = self.new_log(2)
        call = ToolCall("p1", "bash", {"command": "x"})
        log.append(ev.MODEL_RESPONSE, {"response": ModelResponse(None, [call], Usage(), "m").to_dict()})
        st = project(log.events(), "main")
        self.assertEqual([c.id for c in st.pending_tool_calls()], ["p1"])
        msgs = materialize(st)
        assert_wire_valid(self, msgs)
        self.assertIn("No result recorded", msgs[-1].content)

    def test_clearing_is_batched_and_recorded(self):
        log = self.new_log(20, size=4000)
        eng = ContextEngine(window_tokens=40_000, max_output_tokens=4_000, clear_at=0.3, compact_at=0.99, keep_recent_tool_results=4)
        msgs = eng.prepare(log, "main", SYSTEM, [])
        cleared = log.events(types=[ev.CONTEXT_CLEARED])
        self.assertEqual(len(cleared), 1)
        stubs = [m for m in msgs if m.role == "tool" and "Output cleared" in (m.content or "")]
        self.assertEqual(len(stubs), 16)
        kept = [m for m in msgs if m.role == "tool" and "Output cleared" not in (m.content or "")]
        self.assertEqual(len(kept), 4)
        assert_wire_valid(self, msgs)
        # Re-preparing without new events must not clear again (cache-shape stability).
        eng.prepare(log, "main", SYSTEM, [])
        self.assertEqual(len(log.events(types=[ev.CONTEXT_CLEARED])), 1)

    def test_compaction_uses_summary_and_keeps_pairs(self):
        log = self.new_log(20, size=4000)
        summ = ScriptedClient([make_response("## Task progress\n" + "Summary of turns 0-13. " * 10)])
        eng = ContextEngine(window_tokens=30_000, max_output_tokens=4_000, clear_at=0.99, compact_at=0.5, keep_recent_turns=6, summarizer=summ)
        msgs = eng.prepare(log, "main", SYSTEM, [])
        comp = log.events(types=[ev.CONTEXT_COMPACTED])
        self.assertEqual(len(comp), 1)
        self.assertEqual(comp[0].data["method"], "llm")
        self.assertEqual(msgs[0].content, "TASK: do it")  # task stays pinned
        self.assertIn("Context summary", msgs[1].content)
        self.assertEqual(sum(1 for m in msgs if m.role == "assistant"), 6)
        assert_wire_valid(self, msgs)
        self.assertEqual(summ.requests[0].purpose, "compaction")

    def test_compaction_falls_back_when_summarizer_fails(self):
        log = self.new_log(12, size=4000)
        summ = ScriptedClient([RuntimeError("down")])
        eng = ContextEngine(window_tokens=25_000, max_output_tokens=4_000, clear_at=0.99, compact_at=0.4, keep_recent_turns=2, summarizer=summ)
        msgs = eng.prepare(log, "main", SYSTEM, [])
        comp = log.events(types=[ev.CONTEXT_COMPACTED])
        self.assertEqual(comp[0].data["method"], "fallback")
        self.assertIn("Called bash", msgs[1].content)
        assert_wire_valid(self, msgs)

    def test_emergency_truncation_fits_budget(self):
        log = EventLog(self.tmp / "e" / "events.jsonl")
        self.addCleanup(log.close)
        log.append(ev.MESSAGE_USER, {"content": "TASK", "source": "task"})
        add_turn(log, 0, "y" * 200_000)
        eng = ContextEngine(window_tokens=20_000, max_output_tokens=4_000, keep_recent_turns=1)
        msgs = eng.prepare(log, "main", SYSTEM, [])
        self.assertLessEqual(eng.last_estimate, eng.budget)
        self.assertIn("truncated to fit", msgs[-1].content)

    def test_calibration_moves_estimates(self):
        eng = ContextEngine(window_tokens=100_000, max_output_tokens=1000, model_name="m")
        base = eng.estimate(SYSTEM, [Message("user", "x" * 3400)], [])
        eng.observe_usage(base, base * 2)
        self.assertGreater(eng.estimate(SYSTEM, [Message("user", "x" * 3400)], []), base * 1.5)
