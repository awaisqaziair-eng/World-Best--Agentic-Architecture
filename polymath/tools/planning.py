"""Planning and control tools: todo (explicit plan), notes (durable scratchpad),
finish (explicit, structured termination) and ask_user (input_required)."""

from __future__ import annotations

from typing import Any

from .. import events as ev
from .base import Tool, ToolContext, ToolError, ToolOutput

_MARK = {"completed": "[x]", "in_progress": "[>]", "pending": "[ ]"}


def render_plan(items: list[dict[str, Any]]) -> str:
    if not items:
        return "(plan is empty)"
    done = sum(1 for i in items if i.get("status") == "completed")
    lines = [f"Plan ({done}/{len(items)} done):"]
    for n, it in enumerate(items, 1):
        lines.append(f"{_MARK.get(it.get('status', 'pending'), '[ ]')} {n}. {it.get('content', '')}")
    return "\n".join(lines)


class TodoTool(Tool):
    name = "todo"
    description = """\
Write your plan as a checklist (send the full list each time; it replaces the previous one). Use it for
tasks with more than a few steps, sent together with your first actions; keep one item in_progress.
The plan survives context compaction."""
    parameters = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "minLength": 1},
                        "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                    },
                    "required": ["content", "status"],
                },
            }
        },
        "required": ["items"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        items = args["items"]
        ctx.state["plan"] = items
        if ctx.events is not None:
            ctx.events.append(ev.PLAN_UPDATED, {"items": items}, agent=ctx.agent_id)
        n_prog = sum(1 for i in items if i["status"] == "in_progress")
        warn = "\nNote: more than one item is in_progress; focus on one at a time." if n_prog > 1 else ""
        return ToolOutput(render_plan(items) + warn)


class NotesTool(Tool):
    name = "notes"
    description = """\
Durable scratchpad that survives context compaction: key facts, values, file locations, decisions.
Actions: append, read, write (replaces all notes)."""
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["append", "read", "write"]},
            "content": {"type": "string"},
        },
        "required": ["action"],
    }

    @staticmethod
    def path(ctx: ToolContext):
        return ctx.session_dir / f"NOTES.{ctx.agent_id}.md"

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        p = self.path(ctx)
        p.parent.mkdir(parents=True, exist_ok=True)
        action = args["action"]
        if action == "read":
            return ToolOutput(p.read_text(encoding="utf-8") if p.exists() else "(no notes yet)")
        content = args.get("content")
        if not content:
            raise ToolError(f"`content` is required for action '{action}'")
        if action == "append":
            with open(p, "a", encoding="utf-8") as fh:
                fh.write(content.rstrip() + "\n")
        else:
            p.write_text(content.rstrip() + "\n", encoding="utf-8")
        size = len(p.read_text(encoding="utf-8"))
        return ToolOutput(f"Notes saved ({size:,} chars total).")


def read_notes(ctx: ToolContext) -> str:
    p = NotesTool.path(ctx)
    return p.read_text(encoding="utf-8") if p.exists() else ""


class FinishTool(Tool):
    name = "finish"
    terminal = True
    description = """\
End the task and submit the result. Call only when the work is complete and verified (or definitively
impossible — then say why). `answer`: the direct result first (exact values, names, paths), then briefly
how you verified it and any caveats. `artifacts`: files you created or changed."""
    parameters = {
        "type": "object",
        "properties": {
            "answer": {"type": "string", "minLength": 1},
            "artifacts": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["answer"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        ctx.state["finish"] = {"answer": args["answer"], "artifacts": list(args.get("artifacts") or [])}
        return ToolOutput("Final answer submitted.")


class AskUserTool(Tool):
    name = "ask_user"
    description = """\
Ask the human a clarifying question — only when the task is genuinely ambiguous and a wrong guess would
be costly. If no human is available you will be told to assume: then state the assumption and proceed."""
    parameters = {
        "type": "object",
        "properties": {"question": {"type": "string", "minLength": 1}},
        "required": ["question"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        if ctx.ask_user is None:
            return ToolOutput(
                "No human is available in this run. Make the most reasonable assumption, state it explicitly "
                "in your final answer, and continue."
            )
        answer = ctx.ask_user(args["question"])
        if not answer:
            return ToolOutput("The user did not answer. Make a reasonable assumption, state it, and continue.")
        return ToolOutput(f"User answered: {answer}")
