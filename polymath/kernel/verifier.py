"""Completion verification (the evaluator half of evaluator–optimiser).

When the agent calls ``finish`` the kernel asks the verifier whether to accept:

1. **Command check** (deterministic, preferred): run ``task.verify_command`` in
   the workspace; exit status 0 ⇒ pass. Output tail is fed back on failure.
2. **Criteria judge** (model-based): when acceptance criteria exist, a separate
   judge call (utility model, temperature 0, no access to the agent's
   reasoning) checks the answer and the artifacts' contents against each
   criterion and returns structured JSON.

A failed verification is returned to the agent as feedback; after
``max_verify_rounds`` the last answer is accepted and the failure recorded —
verification can improve a result but never deadlock a run.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .. import jsonutil
from ..gateway.base import ChatRequest, ModelClient
from ..tools.terminal import hermetic_env
from ..types import Message, TaskSpec, Verification

JUDGE_SYSTEM = """\
You are a strict, fair verifier. Decide whether an agent's submitted result satisfies EVERY acceptance
criterion of the task. Judge only from the evidence provided (the answer and file contents). Do not
reward effort or plausible-sounding claims; require evidence. Reply with ONLY JSON:
{"passed": true|false, "failed_criteria": ["..."], "feedback": "specific, actionable fixes (empty if passed)"}"""


class Verifier:
    def __init__(self, *, judge: ModelClient | None, workspace: Path, command_timeout_s: float = 600.0) -> None:
        self.judge = judge
        self.workspace = workspace
        self.command_timeout_s = command_timeout_s

    def applicable(self, task: TaskSpec, mode: str) -> bool:
        if mode == "off":
            return False
        if task.verify_command:
            return True
        if task.acceptance_criteria and self.judge is not None:
            return True
        return mode == "always" and self.judge is not None

    def verify(self, task: TaskSpec, answer: str, artifacts: list[str], round_: int) -> Verification:
        if task.verify_command:
            return self._command(task.verify_command, round_)
        return self._judge(task, answer, artifacts, round_)

    def _command(self, cmd: str, round_: int) -> Verification:
        try:
            p = subprocess.run(
                ["bash", "-c", cmd],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=self.command_timeout_s,
                env=hermetic_env(),
                stdin=subprocess.DEVNULL,
            )
            out = (p.stdout + p.stderr)[-4000:]
            return Verification(p.returncode == 0, "command", f"`{cmd}` exited {p.returncode}\n{out}", round_)
        except subprocess.TimeoutExpired:
            return Verification(False, "command", f"`{cmd}` timed out after {self.command_timeout_s:.0f}s", round_)

    def _judge(self, task: TaskSpec, answer: str, artifacts: list[str], round_: int) -> Verification:
        assert self.judge is not None
        evidence = []
        for a in artifacts[:8]:
            p = Path(a) if Path(a).is_absolute() else self.workspace / a
            if p.is_file():
                try:
                    body = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if len(body) > 6000:
                    body = body[:4000] + f"\n…[{len(body) - 6000:,} chars omitted]…\n" + body[-2000:]
                evidence.append(f"### File: {a}\n{body}")
            else:
                evidence.append(f"### File: {a}\n(MISSING — file does not exist)")
        criteria = task.acceptance_criteria or ["The answer fully and correctly accomplishes the task as stated."]
        user = (
            f"# Task\n{task.instruction[:8000]}\n\n# Acceptance criteria\n"
            + "\n".join(f"{i}. {c}" for i, c in enumerate(criteria, 1))
            + f"\n\n# Submitted answer\n{answer[:8000]}\n\n# Artifacts\n"
            + ("\n\n".join(evidence) if evidence else "(none listed)")
        )
        try:
            r = self.judge.complete(ChatRequest(messages=[Message("system", JUDGE_SYSTEM), Message("user", user)], max_tokens=1500, temperature=0.0, purpose="judge"))
            d: dict[str, Any] = jsonutil.loads_lenient(r.content or "", expect=dict)
        except Exception as e:  # judge failure must not fail the task
            return Verification(True, "judge", f"judge unavailable ({type(e).__name__}); accepted without verification", round_)
        passed = bool(d.get("passed"))
        fb = str(d.get("feedback") or "")
        failed = d.get("failed_criteria") or []
        detail = fb if not failed else f"Failed criteria: {failed}\n{fb}"
        return Verification(passed, "judge", detail.strip(), round_)
