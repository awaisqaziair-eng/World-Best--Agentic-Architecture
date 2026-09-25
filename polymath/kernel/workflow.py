"""Deterministic workflows: code-defined control flow with LLM/agent steps.

This is the *deterministic end* of the autonomy spectrum (docs/08-determinism.md).
Control flow — ordering, branching, loops, termination — is ordinary Python;
only the leaves (``llm``, ``agent``) are non-deterministic. Every step's output is
journaled (``workflow.step.completed``), so re-running a workflow on the same
session **skips completed steps and reuses their recorded outputs**, exactly
like Temporal activities: a crash in step 7 resumes at step 7.

Rules for step functions (enforced by convention, documented):
  * all side effects go through ``ctx.llm`` / ``ctx.agent`` / ``ctx.shell``;
  * outputs must be JSON-serialisable;
  * decisions (``when``, ``until``) may depend only on recorded outputs.
"""

from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from .. import events as ev
from .. import jsonutil
from ..events import EventLog
from ..gateway.base import ChatRequest
from ..tools.terminal import hermetic_env
from ..types import Budget, Message, TaskSpec

if TYPE_CHECKING:
    from .runtime import Runtime


class WorkflowContext:
    def __init__(self, rt: "Runtime", log: EventLog, workspace: Path, inputs: dict[str, Any]) -> None:
        self.rt = rt
        self.log = log
        self.workspace = workspace
        self.inputs = inputs
        self.outputs: dict[str, Any] = {}
        self._agent_n = 0

    # ── effects ─────────────────────────────────────────────────────────
    def llm(self, prompt: str, *, system: str | None = None, json_mode: bool = False, max_tokens: int = 4096, temperature: float = 0.2) -> Any:
        msgs = ([Message("system", system)] if system else []) + [Message("user", prompt)]
        r = self.rt.client.complete(ChatRequest(messages=msgs, max_tokens=max_tokens, temperature=temperature, purpose="workflow"))
        self.log.append(ev.MODEL_RESPONSE, {"response": r.to_dict()}, agent="workflow.llm")
        text = (r.content or "").strip()
        return jsonutil.loads_lenient(text) if json_mode else text

    def agent(self, instruction: str, *, name: str, context: str | None = None, budget: Budget | None = None, verify_command: str | None = None) -> dict[str, Any]:
        from .agent import Agent

        self._agent_n += 1
        agent_id = f"wf.{name}.{self._agent_n}"
        a = Agent(self.rt, log=self.log, workspace=self.workspace, agent_id=agent_id, depth=1)
        try:
            res = a.run(TaskSpec(instruction=instruction, workspace=str(self.workspace), context=context, budget=budget or Budget(max_turns=50), verify_command=verify_command))
        finally:
            a.close()
        return res.to_dict()

    def shell(self, cmd: str, timeout: float = 600.0) -> dict[str, Any]:
        try:
            p = subprocess.run(["bash", "-c", cmd], cwd=self.workspace, capture_output=True, text=True, timeout=timeout, env=hermetic_env(), stdin=subprocess.DEVNULL)
            return {"rc": p.returncode, "output": (p.stdout + p.stderr)[-8000:]}
        except subprocess.TimeoutExpired:
            return {"rc": 124, "output": f"timed out after {timeout}s"}

    def parallel(self, thunks: list[Callable[[], Any]], max_workers: int = 4) -> list[Any]:
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(thunks)))) as ex:
            return list(ex.map(lambda f: f(), thunks))

    # ── recorded state ──────────────────────────────────────────────────
    def last(self, step: str) -> Any:
        """Most recent output of a step by base name (across loop iterations)."""
        for k in reversed(list(self.outputs)):
            if k == step or k.endswith("." + step):
                return self.outputs[k]
        return None


@dataclass
class Step:
    name: str
    run: Callable[[WorkflowContext], Any]
    when: Callable[[WorkflowContext], bool] | None = None


@dataclass
class Loop:
    name: str
    body: list[Step]
    until: Callable[[WorkflowContext], bool]
    max_iterations: int = 3


@dataclass
class WorkflowResult:
    name: str
    session_id: str
    outputs: dict[str, Any] = field(default_factory=dict)
    skipped_from_journal: list[str] = field(default_factory=list)
    status: str = "completed"


class Workflow:
    def __init__(self, name: str, steps: list[Step | Loop], description: str = "") -> None:
        self.name = name
        self.steps = steps
        self.description = description

    def run(self, rt: "Runtime", workspace: str | Path, inputs: dict[str, Any], *, session_id: str | None = None) -> WorkflowResult:
        ws = Path(workspace).expanduser().resolve()
        ws.mkdir(parents=True, exist_ok=True)
        log = rt.open_session(session_id) if session_id and (rt.cfg.sessions_path() / session_id).exists() else rt.new_session(session_id)
        journal = {e.data["step"]: e.data["output"] for e in log.events(types=[ev.WORKFLOW_STEP_COMPLETED])}
        if not journal:
            log.append(ev.TASK_SUBMITTED, {"task": {"instruction": f"workflow:{self.name}", "workspace": str(ws), "inputs": inputs}}, agent="workflow")
        ctx = WorkflowContext(rt, log, ws, inputs)
        result = WorkflowResult(self.name, log.session_id)
        try:
            for item in self.steps:
                if isinstance(item, Loop):
                    for i in range(1, item.max_iterations + 1):
                        for s in item.body:
                            self._exec(s, f"{item.name}#{i}.{s.name}", ctx, journal, result)
                        if item.until(ctx):
                            break
                else:
                    self._exec(item, item.name, ctx, journal, result)
        except Exception as e:
            result.status = "failed"
            log.append(ev.HARNESS_NOTE, {"kind": "workflow_failed", "detail": f"{type(e).__name__}: {e}"}, agent="workflow")
            raise
        finally:
            result.outputs = ctx.outputs
            log.close()
        return result

    @staticmethod
    def _exec(step: Step, qname: str, ctx: WorkflowContext, journal: dict[str, Any], result: WorkflowResult) -> None:
        if qname in journal:  # deterministic replay: reuse the recorded output
            ctx.outputs[qname] = journal[qname]
            result.skipped_from_journal.append(qname)
            return
        if step.when is not None and not step.when(ctx):
            return
        ctx.log.append(ev.WORKFLOW_STEP_STARTED, {"step": qname}, agent="workflow")
        out = step.run(ctx)
        jsonutil.canonical(out)  # fail fast on non-serialisable outputs
        ctx.outputs[qname] = out
        ctx.log.append(ev.WORKFLOW_STEP_COMPLETED, {"step": qname, "output": out}, agent="workflow")
