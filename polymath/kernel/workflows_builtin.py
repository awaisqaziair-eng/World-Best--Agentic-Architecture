"""Built-in workflows.

* ``fix-until-green``  — run a check command; while it fails, an agent fixes the
  code; re-check. Deterministic loop, agentic leaves. (inputs: cmd, max_iterations)
* ``research-report``  — plan sub-questions (LLM, JSON) → investigate each with a
  parallel sub-agent → synthesise a cited report (LLM) → write it to disk.
  (inputs: question, output, [sources])
* ``implement-review`` — evaluator–optimiser: an agent implements a spec, a
  reviewer LLM critiques the result as JSON, an agent revises until approved.
  (inputs: spec, [verify_cmd])
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .workflow import Loop, Step, Workflow, WorkflowContext


def fix_until_green(max_iterations: int = 4) -> Workflow:
    def test(c: WorkflowContext) -> dict[str, Any]:
        return c.shell(c.inputs["cmd"])

    def fix(c: WorkflowContext) -> dict[str, Any]:
        t = c.last("test")
        return c.agent(
            f"The check command `{c.inputs['cmd']}` fails. Find the root cause and fix the code so it passes. "
            "Do not modify or delete tests unless a test is itself clearly wrong. Re-run the command to confirm before finishing.\n\n"
            f"Latest output (exit code {t['rc']}):\n```\n{t['output'][-6000:]}\n```",
            name="fix",
        )

    return Workflow(
        "fix-until-green",
        [
            Loop(
                "repair",
                [Step("test", test), Step("fix", fix, when=lambda c: c.last("test")["rc"] != 0)],
                until=lambda c: c.last("test")["rc"] == 0,
                max_iterations=max_iterations,
            ),
            # Only needed when the loop ran out of iterations right after a fix.
            Step("final", test, when=lambda c: c.last("test")["rc"] != 0),
        ],
        "Run a check; while it fails let an agent fix the code.",
    )


def research_report() -> Workflow:
    def plan(c: WorkflowContext) -> dict[str, Any]:
        src = c.inputs.get("sources")
        where = f" The sources are in `{src}`." if src else ""
        d = c.llm(
            f"Break this research question into 3-5 independent, specific sub-questions that together answer it.{where}\n"
            f"Question: {c.inputs['question']}\nReply with only JSON: {{\"sub_questions\": [\"...\"]}}",
            json_mode=True,
            temperature=0.1,
        )
        subs = [s for s in (d.get("sub_questions") or []) if isinstance(s, str)][:5]
        return {"sub_questions": subs or [c.inputs["question"]]}

    def investigate(c: WorkflowContext) -> dict[str, Any]:
        subs = c.last("plan")["sub_questions"]
        src = c.inputs.get("sources")
        ctx_txt = f"Sources to use: {src}" if src else "Use the tools available to find reliable information."
        runs = c.parallel(
            [lambda q=q, i=i: c.agent(f"Research sub-question: {q}\nReturn the findings with exact facts and the source (file path or URL) for each.", name=f"investigate{i}", context=ctx_txt) for i, q in enumerate(subs, 1)]
        )
        return {"findings": [{"question": q, "state": r["state"], "answer": r["answer"]} for q, r in zip(subs, runs)]}

    def synthesize(c: WorkflowContext) -> dict[str, Any]:
        f = c.last("investigate")["findings"]
        body = "\n\n".join(f"### {x['question']}\n{x['answer'] or '(no findings)'}" for x in f)
        text = c.llm(
            f"Write a well-structured Markdown research report answering: {c.inputs['question']}\n"
            "Use ONLY the findings below; keep source citations; flag gaps or conflicts explicitly. Start with a 3-5 sentence executive summary.\n\n"
            f"# Findings\n{body}",
            max_tokens=6000,
        )
        return {"report": text}

    def write(c: WorkflowContext) -> dict[str, Any]:
        out = Path(c.inputs.get("output") or "report.md")
        p = out if out.is_absolute() else c.workspace / out
        p.write_text(c.last("synthesize")["report"] + "\n", encoding="utf-8")
        return {"path": str(p), "chars": p.stat().st_size}

    return Workflow("research-report", [Step("plan", plan), Step("investigate", investigate), Step("synthesize", synthesize), Step("write", write)], "Plan → parallel research → synthesis.")


def implement_review(max_rounds: int = 2) -> Workflow:
    def implement(c: WorkflowContext) -> dict[str, Any]:
        return c.agent(c.inputs["spec"], name="implement", verify_command=c.inputs.get("verify_cmd"))

    def critique(c: WorkflowContext) -> dict[str, Any]:
        listing = c.shell("git status --short 2>/dev/null; find . -type f -not -path './.git/*' | sort | head -80")["output"]
        files = c.shell("for f in $(find . -type f \\( -name '*.py' -o -name '*.js' -o -name '*.ts' -o -name '*.md' \\) -not -path './.git/*' | head -12); do echo \"=== $f\"; head -c 5000 \"$f\"; done")["output"]
        d = c.llm(
            "You are a demanding senior reviewer. Review the implementation against the spec. Reply with ONLY JSON "
            '{"approved": true|false, "issues": ["specific, actionable issue", ...]}. Approve only if every requirement is met and there are no bugs.\n\n'
            f"# Spec\n{c.inputs['spec']}\n\n# Workspace\n{listing}\n\n# Files\n{files[-20000:]}",
            json_mode=True,
            temperature=0.0,
        )
        return {"approved": bool(d.get("approved")), "issues": [str(i) for i in d.get("issues") or []]}

    def revise(c: WorkflowContext) -> dict[str, Any]:
        issues = "\n".join(f"- {i}" for i in c.last("critique")["issues"])
        return c.agent(f"A reviewer found these issues with the implementation of the spec below. Fix all of them and verify.\n\n# Issues\n{issues}\n\n# Spec\n{c.inputs['spec']}", name="revise", verify_command=c.inputs.get("verify_cmd"))

    return Workflow(
        "implement-review",
        [
            Step("implement", implement),
            Loop("review", [Step("critique", critique), Step("revise", revise, when=lambda c: not c.last("critique")["approved"])], until=lambda c: c.last("critique")["approved"], max_iterations=max_rounds),
        ],
        "Implement, then critique/revise until approved.",
    )


WORKFLOWS = {"fix-until-green": fix_until_green, "research-report": research_report, "implement-review": implement_review}
