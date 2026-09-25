"""Agent kernel behaviour with scripted models (fully deterministic)."""

from __future__ import annotations

from polymath import events as ev
from polymath.events import EventLog
from polymath.gateway.base import CONTEXT_OVERFLOW, RETRY_EXHAUSTED, ChatRequest, ModelError
from polymath.kernel.agent import Agent
from polymath.types import Budget, ModelResponse, TaskSpec, ToolCall, Usage

from .helpers import R, TempDirTest, bash, finish


class TestKernel(TempDirTest):
    def types(self, rt):
        sid = rt.list_sessions()[0]["session_id"]
        return [e.type for e in EventLog.load(rt.session_dir(sid) / "events.jsonl")]

    def test_happy_path(self):
        rt, cl = self.runtime([bash("echo hello > out.txt"), finish("wrote it", ["out.txt"])])
        res = rt.run("write hello", self.ws)
        self.assertEqual((res.state, res.stop_reason, res.turns, res.tool_calls), ("completed", "finished", 2, 2))
        self.assertEqual((self.ws / "out.txt").read_text(), "hello\n")
        self.assertEqual(res.artifacts, ["out.txt"])
        t = self.types(rt)
        self.assertEqual(t[:4], [ev.SESSION_STARTED, ev.TASK_SUBMITTED, ev.MESSAGE_USER, ev.TASK_STATE])
        self.assertEqual(t[-1], ev.TASK_COMPLETED)
        # System prompt is stable (cache-shape): no workspace path or date inside it.
        sys_prompt = cl.requests[0].messages[0].content
        self.assertNotIn(str(self.ws), sys_prompt)
        self.assertIn(str(self.ws), cl.requests[0].messages[1].content)

    def test_final_looking_prose_is_accepted_immediately(self):
        rt, cl = self.runtime([R("Done. `out.txt` was written and verified; the answer is 4.")])
        res = rt.run("2+2?", self.ws)
        self.assertEqual((res.state, res.stop_reason, res.turns), ("completed", "text_answer", 1))

    def test_intent_prose_is_nudged_then_accepted(self):
        # Turn 1 announces intent → nudged. Turn 2 is intent again, but the grace limit (2) is
        # reached, so the prose is accepted rather than looping forever (termination, I3).
        rt, cl = self.runtime([R("I found the config. Let me check the tests next."), R("I'll look at it now:")])
        res = rt.run("x", self.ws)
        self.assertEqual((res.stop_reason, res.turns), ("text_answer", 2))
        self.assertIn("without calling a tool", cl.requests[1].messages[-1].content)

    def test_looks_final_heuristic(self):
        from polymath.kernel.agent import looks_final

        for final in ["4", "The file has been created.", "**Result:** 42 primes.\n\nVerified with a second script.", "Heapsort is O(n log n) and not stable."]:
            self.assertTrue(looks_final(final), final)
        for intent in ["Let me run the tests.", "Now I'll fix the bug", "Next, I will write the file.", "Here is the plan:", "Good. I'm going to refactor utils.py"]:
            self.assertFalse(looks_final(intent), intent)

    def test_minimal_mode_accepts_prose_immediately(self):
        rt, _ = self.runtime([R("4")])
        res = rt.run("2+2?", self.ws, mode="minimal")
        self.assertEqual((res.state, res.turns), ("completed", 1))

    def test_empty_responses_fail_cleanly(self):
        rt, _ = self.runtime([R(""), R(None), R("   ")])
        res = rt.run("x", self.ws)
        self.assertEqual((res.state, res.stop_reason), ("failed", "no_progress"))

    def test_length_cutoff_nudges(self):
        rt, cl = self.runtime([R("partial...", finish_reason="length"), finish("ok")])
        res = rt.run("x", self.ws)
        self.assertEqual(res.state, "completed")
        self.assertIn("output-length limit", cl.requests[1].messages[-1].content)

    def test_bad_tool_calls_are_recoverable(self):
        bad = ToolCall("b1", "bash", {}, raw_arguments="{oops", parse_error="bad json")
        rt, cl = self.runtime([R(tool_calls=[("nope", {})]), R(tool_calls=[bad]), finish("fine")])
        res = rt.run("x", self.ws)
        self.assertEqual(res.state, "completed")
        tool_msgs = [m for m in cl.requests[2].messages if m.role == "tool"]
        self.assertIn("unknown tool", tool_msgs[0].content)
        self.assertIn("not valid JSON", tool_msgs[1].content)

    def test_loop_detection_injects_guidance_once(self):
        rt, cl = self.runtime([bash("cat missing.txt")] * 4 + [finish("gave up")], loop_repeat_threshold=3)
        rt.run("x", self.ws)
        notes = [m.content for m in cl.requests[-1].messages if m.role == "user" and "identical" in (m.content or "")]
        self.assertEqual(len(notes), 1)

    def test_error_streak_guidance(self):
        rt, cl = self.runtime([R(tool_calls=[("read_file", {"path": f"missing{i}"})]) for i in range(4)] + [finish("x")])
        rt.run("x", self.ws)
        self.assertTrue(any("last 4 tool calls failed" in (m.content or "") for m in cl.requests[-1].messages))

    def test_turn_budget_wrap_up(self):
        rt, cl = self.runtime([bash("echo 1"), bash("echo 2"), bash("echo 3"), finish("partial: did 3 steps")])
        res = rt.run("x", self.ws, budget=Budget(max_turns=3))
        self.assertEqual((res.state, res.stop_reason), ("failed", "budget_exhausted"))
        self.assertEqual(res.answer, "partial: did 3 steps")
        self.assertEqual([t["function"]["name"] for t in cl.requests[-1].tools], ["finish"])

    def test_budget_warning_at_80_percent(self):
        rt, cl = self.runtime([bash(f"echo {i}") for i in range(4)] + [finish("ok")])
        rt.run("x", self.ws, budget=Budget(max_turns=5))
        self.assertTrue(any("of your budget" in (m.content or "") for m in cl.requests[-1].messages))

    def test_verify_command_feedback_loop(self):
        rt, cl = self.runtime([finish("done?"), bash("touch ok.flag"), finish("done!")])
        res = rt.run("make the flag", self.ws, verify_command="test -f ok.flag")
        self.assertEqual((res.state, res.stop_reason), ("completed", "finished"))
        self.assertEqual([v.passed for v in res.verification], [False, True])
        self.assertIn("did not pass verification", cl.requests[1].messages[-1].content)

    def test_verification_gives_up_after_max_rounds(self):
        rt, _ = self.runtime([finish("a"), finish("b")], max_verify_rounds=2)
        res = rt.run("x", self.ws, verify_command="false")
        self.assertEqual((res.state, res.stop_reason, res.answer), ("completed", "finished_unverified", "b"))

    def test_judge_verification(self):
        judge = [R('{"passed": false, "failed_criteria": ["mentions 42"], "feedback": "say 42"}'), R('{"passed": true}')]
        rt, cl = self.runtime([finish("it is 41"), finish("it is 42")], utility=judge)
        res = rt.run("answer", self.ws, acceptance_criteria=["The answer states 42"])
        self.assertEqual(res.answer, "it is 42")
        self.assertEqual([v.method for v in res.verification], ["judge", "judge"])
        self.assertIn("say 42", cl.requests[1].messages[-1].content)

    def test_model_error_fails_cleanly(self):
        rt, _ = self.runtime([ModelError("down", kind=RETRY_EXHAUSTED)])
        res = rt.run("x", self.ws)
        self.assertEqual((res.state, res.stop_reason), ("failed", "model_error"))

    def test_context_overflow_triggers_compaction_and_retry(self):
        rt, cl = self.runtime(
            [bash("echo a"), bash("echo b"), ModelError("too long", kind=CONTEXT_OVERFLOW), finish("ok")],
            utility=[R("## Task progress\nran echo a and echo b; outputs a and b. " * 3)],
            keep_recent_turns=1,
        )
        res = rt.run("x", self.ws)
        self.assertEqual(res.state, "completed")
        sid = rt.list_sessions()[0]["session_id"]
        self.assertEqual(len(EventLog.load(rt.session_dir(sid) / "events.jsonl") and [e for e in EventLog.load(rt.session_dir(sid) / "events.jsonl") if e.type == ev.CONTEXT_COMPACTED]), 1)

    def test_resume_executes_pending_calls(self):
        rt, _ = self.runtime([])
        log = rt.new_session("crashed")
        task = TaskSpec("make file", str(self.ws))
        log.append(ev.TASK_SUBMITTED, {"task": task.to_dict(), "system_prompt": "SYS"})
        log.append(ev.MESSAGE_USER, {"content": "TASK", "source": "task"})
        call = ToolCall("k1", "bash", {"command": "echo resumed >> marker.txt"})
        log.append(ev.MODEL_RESPONSE, {"response": ModelResponse(None, [call], Usage(5, 1, requests=1), "m").to_dict()})
        log.close()  # "crash": the tool never ran
        rt2, cl2 = self.runtime([finish("resumed ok")])
        res = rt2.resume("crashed")
        self.assertEqual((res.state, res.answer), ("completed", "resumed ok"))
        self.assertEqual((self.ws / "marker.txt").read_text(), "resumed\n")
        self.assertEqual(cl2.requests[0].messages[0].content, "SYS")  # original system prompt restored
        again = rt2.resume("crashed")  # idempotent on a finished session
        self.assertEqual(again.answer, "resumed ok")
        self.assertEqual((self.ws / "marker.txt").read_text(), "resumed\n")

    def test_resume_after_model_error_continues(self):
        # Regression (found live): a run killed by a provider outage must be resumable.
        rt, _ = self.runtime([bash("echo step1 > s.txt"), ModelError("HTTP 500 x7", kind=RETRY_EXHAUSTED)])
        res = rt.run("x", self.ws, session_id="outage")
        self.assertEqual((res.state, res.stop_reason), ("failed", "model_error"))
        rt2, cl2 = self.runtime([finish("recovered")])
        res2 = rt2.resume("outage")
        self.assertEqual((res2.state, res2.answer, res2.turns), ("completed", "recovered", 2))
        self.assertEqual([m.role for m in cl2.requests[0].messages][-2:], ["assistant", "tool"])  # context intact

    def test_budget_exhausted_needs_new_budget_to_resume(self):
        rt, _ = self.runtime([bash("echo 1"), finish("partial")])
        res = rt.run("x", self.ws, budget=Budget(max_turns=1), session_id="tight")
        self.assertEqual(res.stop_reason, "budget_exhausted")
        rt2, cl2 = self.runtime([finish("done with more budget")])
        self.assertEqual(rt2.resume("tight").stop_reason, "budget_exhausted")  # no new budget: unchanged
        self.assertEqual(cl2.requests, [])
        self.assertEqual(rt2.resume("tight", budget=Budget(max_turns=10)).answer, "done with more budget")

    def test_resume_excludes_downtime_from_wall_budget(self):
        import json as _json
        import time as _time

        d = self.tmp / "home" / "sessions" / "old"
        d.mkdir(parents=True)
        t0 = _time.time() - 7200  # the process died two hours ago
        task = TaskSpec("x", str(self.ws), budget=Budget(max_wall_s=600))
        evs = [
            {"seq": 1, "ts": t0, "type": ev.TASK_SUBMITTED, "agent": "main", "data": {"task": task.to_dict(), "system_prompt": "S"}},
            {"seq": 2, "ts": t0 + 1, "type": ev.MESSAGE_USER, "agent": "main", "data": {"content": "T", "source": "task"}},
        ]
        (d / "events.jsonl").write_text("".join(_json.dumps(e) + "\n" for e in evs))
        rt, _ = self.runtime([finish("fine")])
        res = rt.resume("old")
        self.assertEqual((res.state, res.answer), ("completed", "fine"))
        self.assertLess(res.duration_s, 60)

    def test_delegation_runs_parallel_subagents(self):
        def script(req: ChatRequest) -> ModelResponse:
            sys = req.messages[0].content
            task = req.messages[1].content
            if "# Your role: sub-agent" in sys:  # the main prompt also mentions sub-agents
                topic = "alpha" if "alpha" in task else "beta"
                if not any(m.role == "tool" for m in req.messages):
                    return bash(f"echo {topic}-result > {topic}.txt")
                return finish(f"{topic} done")
            if not any(m.role == "tool" for m in req.messages):
                return R(tool_calls=[("delegate", {"tasks": [{"task": "Research topic alpha thoroughly"}, {"task": "Research topic beta thoroughly"}]})])
            report = [m.content for m in req.messages if m.role == "tool"][0]
            return finish("combined: " + report.replace("\n", " ")[:300])

        rt, _ = self.runtime([script] * 20)
        rt.client.repeat_last = True
        res = rt.run("research alpha and beta", self.ws)
        self.assertEqual(res.state, "completed")
        self.assertIn("alpha done", res.answer)
        self.assertIn("beta done", res.answer)
        self.assertTrue((self.ws / "alpha.txt").exists() and (self.ws / "beta.txt").exists())
        sid = rt.list_sessions()[0]["session_id"]
        evs = EventLog.load(rt.session_dir(sid) / "events.jsonl")
        self.assertEqual({e.agent for e in evs} >= {"main", "main.1", "main.2"}, True)
        self.assertEqual(len([e for e in evs if e.type == ev.SUBAGENT_FINISHED]), 2)
        self.assertEqual(res.usage.requests, 6)  # parent 2 + children 2x2, aggregated

    def test_subagent_cannot_delegate_beyond_depth(self):
        rt, _ = self.runtime([])
        log = rt.new_session()
        child = Agent(rt, log=log, workspace=self.ws, agent_id="main.1", depth=1)
        self.assertIsNone(child.registry.get("delegate"))
        child.close()
        log.close()

    def test_replay_reproduces_run(self):
        rt, _ = self.runtime([bash("echo 7 > n.txt"), bash("cat n.txt"), finish("n is 7")])
        res = rt.run("x", self.ws)
        ws2 = self.tmp / "ws_replay"
        ws2.mkdir()
        rt2, _ = self.runtime([])
        res2, log2 = rt2.replay(res.session_id, workspace=str(ws2))
        self.assertEqual((res2.answer, res2.turns, res2.tool_calls), (res.answer, res.turns, res.tool_calls))
        orig = [e.data["result"]["output"].split("\n")[0] for e in EventLog.load(rt.session_dir(res.session_id) / "events.jsonl") if e.type == ev.TOOL_RESULT]
        rep = [e.data["result"]["output"].split("\n")[0] for e in EventLog.load(log2.path) if e.type == ev.TOOL_RESULT]
        self.assertEqual(orig, rep)

    def test_trace_spans_follow_genai_conventions(self):
        from polymath.observability import load_spans

        rt, _ = self.runtime([bash("true"), finish("x")])
        res = rt.run("x", self.ws)
        spans = load_spans(rt.session_dir(res.session_id) / "trace.jsonl")
        ops = sorted(s["attributes"]["gen_ai.operation.name"] for s in spans)
        self.assertEqual(ops, ["chat", "chat", "execute_tool", "execute_tool", "invoke_agent"])
        root = next(s for s in spans if s["attributes"]["gen_ai.operation.name"] == "invoke_agent")
        self.assertTrue(all(s["parentSpanId"] == root["spanId"] for s in spans if s is not root))
