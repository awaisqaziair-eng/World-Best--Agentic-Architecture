"""The agent kernel — the loop that turns a model into an agent.

    ┌──────────────── one turn ─────────────────┐
    │ budget check → context.prepare → model →  │
    │ journal response → execute tools →        │
    │ journal results → health checks →         │
    │ finish? → verify → complete | feedback    │
    └───────────────────────────────────────────┘

Invariants (docs/04-harness-specification.md):
  I1  Every model response and tool result is journaled before it influences
      anything else. The model's view is a pure projection of the journal.
  I2  Tool calls and results always stay paired on the wire.
  I3  The loop always terminates: every path either makes a model call that
      consumes budget, or ends the run.
  I4  A tool can never crash the kernel; a model failure ends the run cleanly
      with state ``failed`` and the session remains resumable.
"""

from __future__ import annotations

import re
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from .. import events as ev
from .. import jsonutil
from ..context import ContextEngine, project
from ..events import EventLog
from ..gateway.base import CONTEXT_OVERFLOW, ChatRequest, ModelClient, ModelError
from ..tools import ToolContext, ToolRegistry, build_registry
from ..tools.planning import read_notes
from ..tools.skills import skills_index
from ..types import (
    COMPLETED,
    FAILED,
    STOP_BUDGET,
    STOP_FINISHED,
    STOP_MODEL_ERROR,
    STOP_NO_PROGRESS,
    STOP_TEXT_ANSWER,
    WORKING,
    Budget,
    Message,
    RunResult,
    TaskSpec,
    ToolCall,
    ToolResult,
    Usage,
    Verification,
)
from .prompts import build_system_prompt, build_task_message
from .router import TaskProfile, heuristic_profile, llm_profile
from .verifier import Verifier

if TYPE_CHECKING:
    from .runtime import Runtime

STOP_UNVERIFIED = "finished_unverified"
# Failures that a later attempt can plausibly get past (provider outage, flaky model, crashed child).
RESUMABLE_STOPS = frozenset({STOP_MODEL_ERROR, STOP_NO_PROGRESS, "crash"})

_INTENT_TAIL = re.compile(
    r"(let me|let's|i['’]ll|i will|i am going to|i['’]m going to|i need to|next,? i|now i|first,? i|i['’]m now)\b[^\n]*$",
    re.I,
)


def looks_final(text: str) -> bool:
    """Heuristic: does a prose-only reply read as a finished answer rather than a
    statement of what the model intends to do next?

    Measured motivation (v1 GLM-5.3 run): all 4 prose-only replies were complete
    final answers, so the confirmation nudge cost a full-context turn each time.
    """
    t = text.strip()
    if not t:
        return False
    if t.endswith(":"):
        return False
    last = re.split(r"(?<=[.!?])\s+|\n+", t)[-1]
    return not _INTENT_TAIL.search(last)


NUDGE_TEXT = (
    "[harness] You replied without calling a tool. If the task is complete, call `finish` with your final "
    "answer (you can reuse the text above). Otherwise continue working with tools."
)
NUDGE_EMPTY = "[harness] Your last reply was empty. Continue the task: call a tool, or call `finish` if you are done."
NUDGE_LENGTH = (
    "[harness] Your last reply hit the output-length limit and was cut off. Continue, and produce large "
    "content in smaller pieces (e.g. write a big file in several parts)."
)


def usage_tree(log: EventLog, agent_id: str) -> Usage:
    """Usage of an agent plus all its descendants (sub-agents)."""
    u = Usage()
    prefix = agent_id + "."
    for e in log.events(types=[ev.MODEL_RESPONSE]):
        if e.agent == agent_id or e.agent.startswith(prefix):
            u.add(Usage.from_dict(e.data["response"].get("usage")))
    return u


class Agent:
    def __init__(
        self,
        rt: "Runtime",
        *,
        log: EventLog,
        workspace: str | Path,
        agent_id: str = "main",
        depth: int = 0,
        client: ModelClient | None = None,
        registry: ToolRegistry | None = None,
        mode: str = "full",  # full | minimal (ablation baseline)
        ask_user: Callable[[str], str | None] | None = None,
        parent_span: Any = None,
    ) -> None:
        self.rt = rt
        self.cfg = rt.cfg
        self.log = log
        self.agent_id = agent_id
        self.depth = depth
        self.minimal = mode == "minimal"
        self.mode = mode
        self.workspace = Path(workspace).expanduser().resolve()
        self.client = client or rt.client
        self.registry = registry or build_registry(
            self.cfg,
            depth=depth,
            skills=None if self.minimal else rt.skills,
            memory=None if self.minimal else rt.memory,
            minimal=self.minimal,
        )
        session_dir = log.path.parent
        self.ctx = ToolContext(
            workspace=self.workspace,
            session_dir=session_dir,
            session_id=log.session_id,
            agent_id=agent_id,
            config=self.cfg,
            events=log,
            depth=depth,
            spawn_subagents=self._spawn_subagents if self.registry.get("delegate") else None,
            ask_user=ask_user,
        )
        self.context = ContextEngine(
            window_tokens=self.cfg.window_for(self.client.model),
            max_output_tokens=self.cfg.max_output_for(self.client.model),
            clear_at=self.cfg.clear_at,
            compact_at=self.cfg.compact_at,
            keep_recent_tool_results=self.cfg.keep_recent_tool_results,
            keep_recent_turns=self.cfg.keep_recent_turns,
            clear_min_chars=self.cfg.clear_min_chars,
            summarizer=rt.utility,
            calibrator=rt.calibrator,
            model_name=self.client.model,
            notes_reader=lambda: read_notes(self.ctx),
        )
        self.verifier = Verifier(judge=rt.utility, workspace=self.workspace)
        idx = "" if self.minimal else skills_index(rt.skills)
        self.system = Message("system", build_system_prompt(idx, subagent=depth > 0))
        self.tracer = rt.tracer(log)
        self.parent_span = parent_span
        self.task: TaskSpec | None = None
        self.profile: TaskProfile | None = None
        self._lock = threading.Lock()
        self._sigs: deque[str] = deque(maxlen=self.cfg.loop_window)
        self._warned: set[str] = set()
        self._err_streak = 0
        self._text_turns = 0
        self._empty_turns = 0
        self._verify_round = 0
        self._budget_warned = False
        self._wall_offset = 0.0  # active seconds before this process took over (resume)
        self._wall_start: float | None = None  # None → measure from the session's first event

    # ── public API ──────────────────────────────────────────────────────
    def run(self, task: TaskSpec) -> RunResult:
        self.task = task
        self.workspace.mkdir(parents=True, exist_ok=True)
        if self.agent_id == "main" and self.log.last(ev.SESSION_STARTED) is None:
            self.log.append(
                ev.SESSION_STARTED,
                {"model": self.client.model, "mode": self.mode, "config": _public_config(self.cfg), "version": _version()},
                agent=self.agent_id,
            )
        hints: list[str] = []
        memories: list[str] = []
        playbook: tuple[str, str] | None = None
        if not self.minimal:
            self.profile = self._route(task.instruction)
            # v1.1: inline the top (confidently routed) skill instead of hinting it — measured in v1,
            # models loaded hinted skills ~1.2×/task, each load costing a full-context turn.
            top = next((s for s in self.profile.skills if s in self.rt.skills), None)
            if top is not None:
                playbook = (top, self.rt.skills[top].body)
                self.ctx.state.setdefault("skills_loaded", []).append(top)
            hints = self.profile.hints(set(self.rt.skills) - ({top} if top else set()))
            if self.rt.memory is not None and self.depth == 0:
                from ..tools.memory_tool import format_memory

                memories = [format_memory(r, s) for s, r in self.rt.memory.search(task.instruction, k=3) if s >= 1.5]
        content = build_task_message(task, workspace=self.workspace, memories=memories, hints=hints, playbook=playbook)
        self.log.append(
            ev.TASK_SUBMITTED,
            {
                "task": task.to_dict(),
                "system_prompt": self.system.content,
                "tools": self.registry.names(),
                "model": self.client.model,
                "profile": self.profile.to_dict() if self.profile else None,
            },
            agent=self.agent_id,
        )
        self.log.append(ev.MESSAGE_USER, {"content": content, "source": "task"}, agent=self.agent_id)
        return self._loop()

    def continue_with(self, message: str) -> RunResult:
        """Multi-turn chat: append a user follow-up and keep working."""
        if self.task is None:
            raise RuntimeError("continue_with() requires a prior run()")
        self._text_turns = self._empty_turns = self._verify_round = 0
        self.log.append(ev.MESSAGE_USER, {"content": message, "source": "user"}, agent=self.agent_id)
        return self._loop()

    @classmethod
    def resume(cls, rt: "Runtime", log: EventLog, *, agent_id: str = "main", budget: Budget | None = None, **kw: Any) -> RunResult:
        """Continue a session.

        * completed                          → idempotent: return the recorded result
        * failed (model_error/no_progress/crash) or no terminal event (process died) → continue
        * failed (budget_exhausted)          → continue only when a new ``budget`` is supplied
        """
        st = project(log.events(agent=agent_id), agent_id)
        if st.task is None:
            raise ValueError(f"session {log.session_id} has no task for agent {agent_id}")
        if st.completed is not None:
            stop = st.completed.get("stop_reason")
            if st.completed.get("state") == COMPLETED or (stop == STOP_BUDGET and budget is None) or stop not in RESUMABLE_STOPS | {STOP_BUDGET}:
                return _result_from_dict(st.completed)
        agent = cls(rt, log=log, workspace=st.task["workspace"], agent_id=agent_id, depth=agent_id.count("."), **kw)
        agent.task = TaskSpec.from_dict(st.task)
        if budget is not None:
            agent.task.budget = budget
        submitted = log.last(ev.TASK_SUBMITTED, agent=agent_id)
        if submitted and submitted.data.get("system_prompt"):
            agent.system = Message("system", submitted.data["system_prompt"])  # exact prompt of the original run
        agent.ctx.state["plan"] = st.plan
        # Wall-clock budget counts active time only: downtime between crash and resume is excluded.
        agent._wall_offset = (st.last_ts - st.first_ts) if (st.first_ts and st.last_ts) else 0.0
        agent._wall_start = time.time()
        pending = st.pending_tool_calls()
        log.append(
            ev.HARNESS_NOTE,
            {"kind": "resumed", "detail": f"after {st.completed.get('stop_reason') if st.completed else 'interruption'}; re-executing {len(pending)} pending tool call(s)"},
            agent=agent_id,
        )
        try:
            if pending:
                agent._execute(pending)
            if "finish" in agent.ctx.state:
                done = agent._on_finish()
                if done is not None:
                    return done
            return agent._loop()
        finally:
            agent.close()

    def close(self) -> None:
        self.ctx.close()

    # ── the loop ────────────────────────────────────────────────────────
    def _loop(self) -> RunResult:
        assert self.task is not None
        self.log.append(ev.TASK_STATE, {"state": WORKING}, agent=self.agent_id)
        specs = self.registry.specs()
        force_compact = False
        overflow_retries = 0
        attrs = {"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "polymath", "gen_ai.agent.id": self.agent_id, "gen_ai.conversation.id": self.log.session_id}
        with self.tracer.span(f"invoke_agent {self.agent_id}", attrs, parent=self.parent_span) as agent_span:
            while True:
                st = project(self.log.events(agent=self.agent_id), self.agent_id)
                exhausted = self._budget_check(st)
                if exhausted:
                    return self._wrap_up(exhausted, specs)

                msgs = self.context.prepare(self.log, self.agent_id, self.system, specs, force_compact=force_compact)
                est = self.context.last_estimate
                req = ChatRequest(messages=[self.system, *msgs], tools=specs, purpose="agent")
                if self.rt.console is not None:
                    req.on_progress = lambda n, secs, _a=self.agent_id: self.rt.console.progress(_a, n, secs)
                t0 = time.time_ns()
                try:
                    resp = self.client.complete(req)
                except ModelError as e:
                    self.tracer.record(f"chat {self.client.model}", t0, time.time_ns(), {"gen_ai.operation.name": "chat", "gen_ai.request.model": self.client.model, "error.type": e.kind}, parent=agent_span, error=str(e))
                    self.log.append(ev.MODEL_ERROR, e.to_dict(), agent=self.agent_id)
                    if e.kind == CONTEXT_OVERFLOW and overflow_retries < 3:
                        overflow_retries += 1
                        force_compact = True
                        self.context.window = int(self.context.window * 0.8)  # the declared window was optimistic
                        continue
                    agent_span.fail(str(e))
                    return self._finalize(FAILED, STOP_MODEL_ERROR, None, error=str(e))
                force_compact = False
                self.tracer.record(
                    f"chat {resp.model}",
                    t0,
                    time.time_ns(),
                    {
                        "gen_ai.operation.name": "chat",
                        "gen_ai.provider.name": "openai_compatible",
                        "gen_ai.request.model": self.client.model,
                        "gen_ai.response.model": resp.model,
                        "gen_ai.usage.input_tokens": resp.usage.input_tokens,
                        "gen_ai.usage.output_tokens": resp.usage.output_tokens,
                        "gen_ai.response.finish_reasons": [resp.finish_reason or ""],
                        "polymath.estimated_input_tokens": est,
                        "polymath.attempts": resp.attempts,
                        "polymath.protocol": resp.protocol,
                    },
                    parent=agent_span,
                )
                self.context.observe_usage(est, resp.usage.input_tokens)
                self.log.append(ev.MODEL_RESPONSE, {"response": resp.to_dict(), "est_input_tokens": est}, agent=self.agent_id)

                if resp.tool_calls:
                    self._text_turns = self._empty_turns = 0
                    results = self._execute(resp.tool_calls, parent=agent_span)
                    self._health_checks(resp.tool_calls, results, resp.finish_reason)
                    if "finish" in self.ctx.state:
                        done = self._on_finish()
                        if done is not None:
                            return done
                    continue

                text = (resp.content or "").strip()
                if resp.finish_reason == "length":
                    self._user(NUDGE_LENGTH, "harness")
                    continue
                if text:
                    self._text_turns += 1
                    if self.minimal or looks_final(text) or self._text_turns >= self.cfg.text_answer_grace:
                        self.ctx.state["finish"] = {"answer": text, "artifacts": [], "via": "text"}
                        done = self._on_finish()
                        if done is not None:
                            return done
                        continue
                    self._user(NUDGE_TEXT, "harness")
                else:
                    self._empty_turns += 1
                    if self._empty_turns >= 3:
                        return self._finalize(FAILED, STOP_NO_PROGRESS, None, error="model produced 3 consecutive empty responses")
                    self._user(NUDGE_EMPTY, "harness")

    # ── tools ───────────────────────────────────────────────────────────
    def _execute(self, calls: list[ToolCall], parent: Any = None) -> list[ToolResult]:
        results = self.registry.execute_batch(calls, self.ctx, max_workers=self.cfg.tool_parallelism)
        now_ns = time.time_ns()
        for r in results:
            self.log.append(ev.TOOL_RESULT, {"result": r.to_dict()}, agent=self.agent_id)
            self.tracer.record(
                f"execute_tool {r.name}",
                now_ns - int(r.duration_s * 1e9),
                now_ns,
                {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": r.name, "gen_ai.tool.call.id": r.call_id, "gen_ai.tool.type": "function", "polymath.tool.is_error": r.is_error, "polymath.tool.output_chars": len(r.output)},
                parent=parent,
                error=r.output[:200] if r.is_error else None,
            )
        return results

    def _health_checks(self, calls: list[ToolCall], results: list[ToolResult], finish_reason: str | None) -> None:
        if self.minimal:
            return
        by_id = {r.call_id: r for r in results}
        for c in calls:
            r = by_id.get(c.id)
            if r is None or c.name == "finish":
                continue
            sig = jsonutil.digest([c.name, c.arguments, r.output[:4000]])
            self._sigs.append(sig)
            if self._sigs.count(sig) >= self.cfg.loop_repeat_threshold and sig not in self._warned:
                self._warned.add(sig)
                self.log.append(ev.HARNESS_NOTE, {"kind": "loop_detected", "detail": f"{c.name} repeated {self._sigs.count(sig)}x"}, agent=self.agent_id)
                self._user(
                    f"[harness] You have made the identical `{c.name}` call {self._sigs.count(sig)} times and got the same result. "
                    "Repeating it will not change the outcome. Step back, re-read the output, question your assumptions, and try a different approach.",
                    "harness",
                )
            self._err_streak = self._err_streak + 1 if r.is_error else 0
            if self._err_streak == 4:
                self.log.append(ev.HARNESS_NOTE, {"kind": "error_streak", "detail": "4 consecutive tool errors"}, agent=self.agent_id)
                self._user(
                    "[harness] Your last 4 tool calls failed. Pause and diagnose: re-read the error messages, check your assumptions "
                    "(paths, working directory, argument formats, syntax), and simplify the next step.",
                    "harness",
                )
        if finish_reason == "length" and any(c.parse_error for c in calls):
            self._user(NUDGE_LENGTH, "harness")

    # ── completion ──────────────────────────────────────────────────────
    def _on_finish(self) -> RunResult | None:
        assert self.task is not None
        fin = self.ctx.state.pop("finish")
        answer, artifacts = fin["answer"], list(fin.get("artifacts") or [])
        via_text = fin.get("via") == "text"
        stop = STOP_TEXT_ANSWER if via_text else STOP_FINISHED
        if not self.minimal and self.verifier.applicable(self.task, self.cfg.verify):
            if self._verify_round >= self.cfg.max_verify_rounds:
                return self._finalize(COMPLETED, STOP_UNVERIFIED, answer, artifacts)
            self._verify_round += 1
            v = self.verifier.verify(self.task, answer, artifacts, self._verify_round)
            self.log.append(ev.VERIFICATION, {"verification": v.to_dict()}, agent=self.agent_id)
            if not v.passed:
                if self._verify_round >= self.cfg.max_verify_rounds:
                    return self._finalize(COMPLETED, STOP_UNVERIFIED, answer, artifacts)
                self._user(
                    f"[verifier] Your submission did not pass verification (attempt {self._verify_round}/{self.cfg.max_verify_rounds}).\n"
                    f"{v.detail}\n\nFix the problems, re-check your work, then call `finish` again.",
                    "verifier",
                )
                return None
        return self._finalize(COMPLETED, stop, answer, artifacts)

    def _wrap_up(self, reason: str, specs: list[dict[str, Any]]) -> RunResult:
        self.log.append(ev.HARNESS_NOTE, {"kind": "budget_exhausted", "detail": reason}, agent=self.agent_id)
        answer = None
        if not self.minimal:
            self._user(
                f"[harness] {reason}. Stop working now and call `finish` with your best final answer: what is complete, "
                "the current state, and what remains undone.",
                "harness",
            )
            finish_only = [s for s in specs if s["function"]["name"] == "finish"]
            # Attempt 1: finish is the only tool offered. Attempt 2 (v1.2, measured: Nemotron called a
            # non-offered tool here): force it with a named tool_choice.
            for choice in (None, {"type": "function", "function": {"name": "finish"}}):
                try:
                    msgs = self.context.prepare(self.log, self.agent_id, self.system, finish_only)
                    resp = self.client.complete(ChatRequest(messages=[self.system, *msgs], tools=finish_only, tool_choice=choice, purpose="agent"))
                except ModelError as e:
                    self.log.append(ev.MODEL_ERROR, e.to_dict(), agent=self.agent_id)
                    break
                self.log.append(ev.MODEL_RESPONSE, {"response": resp.to_dict(), "wrap_up": True}, agent=self.agent_id)
                for tc in resp.tool_calls:
                    if tc.name == "finish":
                        answer = str(tc.arguments.get("answer") or "") or answer
                        self.log.append(ev.TOOL_RESULT, {"result": ToolResult(tc.id, tc.name, "Final answer submitted.").to_dict()}, agent=self.agent_id)
                    else:
                        self.log.append(ev.TOOL_RESULT, {"result": ToolResult(tc.id, tc.name, "Not executed: budget exhausted.", is_error=True).to_dict()}, agent=self.agent_id)
                answer = answer or (resp.content or "").strip() or None
                if answer:
                    break
            if not answer:
                answer = self._synthesised_partial_answer(reason)
        return self._finalize(FAILED, STOP_BUDGET, answer, error=reason)

    def _synthesised_partial_answer(self, reason: str) -> str:
        """Deterministic last resort: report plan state and the latest progress notes."""
        st = project(self.log.events(agent=self.agent_id), self.agent_id)
        plan = "\n".join(f"- [{i.get('status')}] {i.get('content')}" for i in st.plan) or "- (no plan recorded)"
        said = [e.msg.content.strip() for e in st.entries if e.msg.role == "assistant" and e.msg.content and e.msg.content.strip()]
        recent = "\n".join(f"> {s[:300]}" for s in said[-3:]) or "> (no progress messages)"
        return f"[Partial result — {reason}; the model did not submit a final answer.]\nPlan state:\n{plan}\nLast progress messages:\n{recent}"

    def _finalize(self, state: str, stop: str, answer: str | None, artifacts: list[str] | None = None, *, error: str | None = None) -> RunResult:
        st = project(self.log.events(agent=self.agent_id), self.agent_id)
        res = RunResult(
            session_id=self.log.session_id,
            agent_id=self.agent_id,
            state=state,
            stop_reason=stop,
            answer=answer,
            artifacts=artifacts or [],
            usage=usage_tree(self.log, self.agent_id),
            turns=st.turns,
            tool_calls=st.tool_calls,
            duration_s=self._active_seconds(st),
            verification=[Verification(**v) for v in st.verifications],
            error=error,
            models_used=st.models_used,
        )
        self.log.append(ev.TASK_STATE, {"state": state, "reason": stop}, agent=self.agent_id)
        self.log.append(ev.TASK_COMPLETED, {"result": res.to_dict()}, agent=self.agent_id)
        return res

    # ── budgets ─────────────────────────────────────────────────────────
    def _budget_check(self, st: Any) -> str | None:
        assert self.task is not None
        b: Budget = self.task.budget
        tokens = usage_tree(self.log, self.agent_id).total
        wall = self._active_seconds(st)
        if st.turns >= b.max_turns:
            return f"Turn budget exhausted ({st.turns}/{b.max_turns} turns)"
        if st.tool_calls >= b.max_tool_calls:
            return f"Tool-call budget exhausted ({st.tool_calls}/{b.max_tool_calls})"
        if tokens >= b.max_tokens:
            return f"Token budget exhausted ({tokens:,}/{b.max_tokens:,} tokens)"
        if wall >= b.max_wall_s:
            return f"Time budget exhausted ({wall:.0f}s/{b.max_wall_s:.0f}s)"
        if not self.minimal and not self._budget_warned:
            frac = max(st.turns / b.max_turns, st.tool_calls / b.max_tool_calls, tokens / b.max_tokens, wall / b.max_wall_s)
            if frac >= 0.8:
                self._budget_warned = True
                self._user(
                    f"[harness] You have used {frac:.0%} of your budget ({st.turns}/{b.max_turns} turns). Prioritise: finish the "
                    "essential parts, verify them, and call `finish` soon.",
                    "harness",
                )
        return None

    def _active_seconds(self, st: Any) -> float:
        if self._wall_start is not None:
            return self._wall_offset + (time.time() - self._wall_start)
        return (time.time() - st.first_ts) if st.first_ts else 0.0

    # ── sub-agents ──────────────────────────────────────────────────────
    def _spawn_subagents(self, specs: list[dict[str, Any]]) -> list[RunResult]:
        assert self.task is not None
        st = project(self.log.events(agent=self.agent_id), self.agent_id)
        pb = self.task.budget
        used = usage_tree(self.log, self.agent_id).total
        n = max(1, len(specs))
        child_budget = Budget(
            max_turns=min(40, pb.max_turns),
            max_tokens=max(150_000, (pb.max_tokens - used) // n),
            max_wall_s=max(120.0, min(1800.0, pb.max_wall_s - ((time.time() - st.first_ts) if st.first_ts else 0))),
            max_tool_calls=min(150, pb.max_tool_calls),
        )
        parent_span = self.tracer.current()

        def run_child(spec: dict[str, Any]) -> RunResult:
            with self._lock:
                k = sum(1 for e in self.log.events(agent=self.agent_id, types=[ev.SUBAGENT_SPAWNED])) + 1
                child_id = f"{self.agent_id}.{k}"
                self.log.append(ev.SUBAGENT_SPAWNED, {"child_agent": child_id, "task": spec}, agent=self.agent_id)
            child = Agent(self.rt, log=self.log, workspace=self.workspace, agent_id=child_id, depth=self.depth + 1, client=self.client, parent_span=parent_span)
            try:
                res = child.run(TaskSpec(instruction=spec["task"], context=spec.get("context"), workspace=str(self.workspace), budget=child_budget))
            except Exception as e:  # a crashing child must not take the parent down
                res = RunResult(self.log.session_id, child_id, FAILED, "crash", None, error=f"{type(e).__name__}: {e}")
            finally:
                child.close()
            self.log.append(ev.SUBAGENT_FINISHED, {"child_agent": child_id, "result": res.to_dict()}, agent=self.agent_id)
            return res

        with ThreadPoolExecutor(max_workers=min(n, self.cfg.max_parallel_subagents)) as ex:
            return list(ex.map(run_child, specs))

    # ── helpers ─────────────────────────────────────────────────────────
    def _user(self, content: str, source: str) -> None:
        self.log.append(ev.MESSAGE_USER, {"content": content, "source": source}, agent=self.agent_id)

    def _route(self, instruction: str) -> TaskProfile:
        mode = self.cfg.router
        if mode == "off":
            return TaskProfile(source="off")
        if mode == "llm" and self.rt.utility is not None:
            return llm_profile(instruction, self.rt.utility)
        return heuristic_profile(instruction)


def _public_config(cfg: Any) -> dict[str, Any]:
    d = cfg.to_dict()
    d.pop("model_params", None)
    return d


def _version() -> str:
    from .. import __version__

    return __version__


def _result_from_dict(d: dict[str, Any]) -> RunResult:
    return RunResult(
        session_id=d["session_id"],
        agent_id=d["agent_id"],
        state=d["state"],
        stop_reason=d["stop_reason"],
        answer=d.get("answer"),
        artifacts=list(d.get("artifacts") or []),
        usage=Usage.from_dict(d.get("usage")),
        turns=int(d.get("turns", 0)),
        tool_calls=int(d.get("tool_calls", 0)),
        duration_s=float(d.get("duration_s", 0.0)),
        verification=[Verification(**v) for v in d.get("verification") or []],
        error=d.get("error"),
        models_used=list(d.get("models_used") or []),
    )
