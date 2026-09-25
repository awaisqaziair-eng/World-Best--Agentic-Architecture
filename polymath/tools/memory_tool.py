"""memory: the agent's interface to long-term, cross-session memory."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..memory import MemoryStore
from .base import Tool, ToolContext, ToolError, ToolOutput


class MemoryTool(Tool):
    name = "memory"
    parallel_safe = True
    description = """\
Cross-session memory. save: durable, reusable knowledge (preferences, conventions, lessons) — not task
details. search: by keywords. list: most recent. delete: by id."""
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["save", "search", "list", "delete"]},
            "content": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "query": {"type": "string"},
            "id": {"type": "string"},
            "k": {"type": "integer", "minimum": 1, "maximum": 50, "default": 5},
        },
        "required": ["action"],
    }

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        action = args["action"]
        if action == "save":
            if not args.get("content"):
                raise ToolError("`content` is required to save a memory")
            rec = self.store.save(args["content"], args.get("tags"), session=ctx.session_id)
            return ToolOutput(f"Saved memory {rec.id}.")
        if action == "delete":
            if not args.get("id"):
                raise ToolError("`id` is required to delete a memory")
            return ToolOutput("Deleted." if self.store.delete(args["id"]) else f"No memory with id {args['id']}.")
        k = int(args.get("k") or 5)
        if action == "search":
            hits = self.store.search(args.get("query") or "", k)
        else:
            hits = [(0.0, r) for r in self.store.all()[-k:][::-1]]
        if not hits:
            return ToolOutput("No memories found.")
        return ToolOutput("\n".join(format_memory(r, s) for s, r in hits))


def format_memory(rec: Any, score: float | None = None) -> str:
    when = datetime.fromtimestamp(rec.ts, tz=timezone.utc).strftime("%Y-%m-%d")
    tags = f" [{', '.join(rec.tags)}]" if rec.tags else ""
    sc = f" (score {score:.2f})" if score else ""
    return f"- ({rec.id}, {when}{tags}{sc}) {rec.content}"
