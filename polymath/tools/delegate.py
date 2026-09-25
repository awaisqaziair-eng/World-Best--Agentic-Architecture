"""delegate: orchestrator → workers. Spawns fresh-context sub-agents in parallel.

Each worker gets a clean context window (only its task + optional context), the
same workspace, and the full toolset minus ``delegate`` at max depth. It returns
a condensed report. The parent pays only for the reports, not for the workers'
exploration — the core economic argument for sub-agents.
"""

from __future__ import annotations

from typing import Any

from .base import Tool, ToolContext, ToolError, ToolOutput


class DelegateTool(Tool):
    name = "delegate"
    description = """\
Run independent sub-tasks in parallel sub-agents, each with a fresh context and the same tools and
workspace; returns one report per sub-agent. Use for genuinely independent work (separate questions,
files, components) or to keep bulky exploration out of your context — not for tightly coupled steps.
Briefs must be self-contained (goal, paths/facts, constraints, what to return): sub-agents cannot see
this conversation. Give each sub-agent a different output file."""
    parameters = {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "properties": {
                        "task": {"type": "string", "minLength": 10},
                        "context": {"type": "string"},
                    },
                    "required": ["task"],
                },
            }
        },
        "required": ["tasks"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        if ctx.spawn_subagents is None:
            raise ToolError("delegation is not available at this depth")
        results = ctx.spawn_subagents(args["tasks"])
        parts = []
        failed = 0
        for i, (spec, res) in enumerate(zip(args["tasks"], results), 1):
            status = res.state if res else "failed"
            failed += status != "completed"
            head = f"## Sub-agent {i} [{status}; {getattr(res, 'turns', 0)} turns]\nTask: {spec['task'][:200]}"
            body = (res.answer if res and res.answer else f"(no answer; error: {getattr(res, 'error', None)})")
            parts.append(f"{head}\nReport:\n{body}")
        return ToolOutput("\n\n".join(parts), is_error=failed == len(results), meta={"n": len(results), "failed": failed})
