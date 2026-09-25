"""Event log → conversation state (pure projection).

``project(events, agent)`` folds one agent's events into a ``ConversationState``;
``materialize(state, ...)`` renders the message list the model will see. Both
are pure functions of the log, so resume and replay reproduce exactly the same
context the original run saw.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .. import events as ev
from ..events import Event
from ..types import Message, ModelResponse, ToolResult, Usage

CLEARED_STUB = "[Output cleared from context to save space ({n:,} chars). Re-run the tool if you need it again.]"


@dataclass
class Entry:
    seq: int
    msg: Message


@dataclass
class ConversationState:
    agent: str
    entries: list[Entry] = field(default_factory=list)
    task: dict[str, Any] | None = None
    task_entry: Entry | None = None
    cleared_upto: int = 0
    compaction: dict[str, Any] | None = None  # latest {upto_seq, summary}
    usage: Usage = field(default_factory=Usage)
    turns: int = 0
    tool_calls: int = 0
    plan: list[dict[str, Any]] = field(default_factory=list)
    state: str | None = None
    completed: dict[str, Any] | None = None
    verifications: list[dict[str, Any]] = field(default_factory=list)
    models_used: list[str] = field(default_factory=list)
    first_ts: float | None = None
    last_ts: float | None = None
    n_compactions: int = 0

    def pending_tool_calls(self) -> list[Any]:
        """Tool calls of the last assistant message that have no result yet."""
        last_asst = None
        for e in reversed(self.entries):
            if e.msg.role == "assistant":
                last_asst = e
                break
        if last_asst is None or not last_asst.msg.tool_calls:
            return []
        done = {e.msg.tool_call_id for e in self.entries if e.seq > last_asst.seq and e.msg.role == "tool"}
        return [tc for tc in last_asst.msg.tool_calls if tc.id not in done]


def project(events: list[Event], agent: str) -> ConversationState:
    st = ConversationState(agent=agent)
    for e in events:
        if e.agent != agent:
            continue
        st.first_ts = st.first_ts or e.ts
        st.last_ts = e.ts
        t, d = e.type, e.data
        if t == ev.TASK_SUBMITTED:
            st.task = d.get("task")
        elif t == ev.MESSAGE_USER:
            entry = Entry(e.seq, Message("user", d.get("content", ""), meta={"source": d.get("source", "harness")}))
            st.entries.append(entry)
            if d.get("source") == "task" and st.task_entry is None:
                st.task_entry = entry
        elif t == ev.MODEL_RESPONSE:
            r = ModelResponse.from_dict(d["response"])
            st.entries.append(
                Entry(e.seq, Message("assistant", r.content, tool_calls=r.tool_calls, meta={"reasoning": r.reasoning, "model": r.model}))
            )
            st.usage.add(r.usage)
            st.turns += 1
            st.tool_calls += len(r.tool_calls)
            if r.model not in st.models_used:
                st.models_used.append(r.model)
        elif t == ev.TOOL_RESULT:
            res = ToolResult.from_dict(d["result"])
            st.entries.append(
                Entry(e.seq, Message("tool", res.output, tool_call_id=res.call_id, name=res.name, meta={"is_error": res.is_error}))
            )
        elif t == ev.CONTEXT_CLEARED:
            st.cleared_upto = max(st.cleared_upto, int(d["upto_seq"]))
        elif t == ev.CONTEXT_COMPACTED:
            st.compaction = {"upto_seq": int(d["upto_seq"]), "summary": d["summary"]}
            st.n_compactions += 1
        elif t == ev.PLAN_UPDATED:
            st.plan = list(d.get("items") or [])
        elif t == ev.TASK_STATE:
            st.state = d.get("state")
        elif t == ev.VERIFICATION:
            st.verifications.append(d.get("verification") or {})
        elif t == ev.TASK_COMPLETED:
            st.completed = d.get("result")
    return st


def visible_entries(st: ConversationState) -> list[Entry]:
    """Entries after the latest compaction point, excluding the pinned task message."""
    upto = st.compaction["upto_seq"] if st.compaction else 0
    return [e for e in st.entries if e.seq > upto and e is not st.task_entry]


def materialize(st: ConversationState, *, clear_min_chars: int = 1200) -> list[Message]:
    msgs: list[Message] = []
    if st.task_entry is not None:
        msgs.append(st.task_entry.msg)
    if st.compaction:
        msgs.append(
            Message(
                "user",
                "[Context summary — earlier work in this task was compacted. This summary replaces it.]\n\n"
                + st.compaction["summary"],
                meta={"source": "compaction"},
            )
        )
    open_calls: dict[str, str] = {}
    for e in visible_entries(st):
        m = e.msg
        if m.role == "tool":
            if m.tool_call_id not in open_calls:
                continue  # orphaned result (its call was compacted away) — drop to keep the wire valid
            open_calls.pop(m.tool_call_id, None)
            if e.seq <= st.cleared_upto and m.content and len(m.content) > clear_min_chars:
                head = m.content[:300].rstrip()
                m = Message("tool", head + "\n" + CLEARED_STUB.format(n=len(m.content)), tool_call_id=m.tool_call_id, name=m.name, meta=m.meta)
        elif m.role == "assistant":
            # Any calls left open by the previous assistant message get a synthetic result.
            msgs.extend(_synthetic_results(open_calls))
            open_calls = {tc.id: tc.name for tc in m.tool_calls}
        else:
            msgs.extend(_synthetic_results(open_calls))
            open_calls = {}
        msgs.append(m)
    msgs.extend(_synthetic_results(open_calls))
    return msgs


def _synthetic_results(open_calls: dict[str, str]) -> list[Message]:
    out = [
        Message("tool", "[No result recorded for this call (the run was interrupted). Re-run it if needed.]", tool_call_id=cid, name=name)
        for cid, name in open_calls.items()
    ]
    open_calls.clear()
    return out


def render_transcript(entries: list[Entry], *, max_tool_chars: int = 2000, max_text_chars: int = 4000) -> str:
    """Human/LLM-readable transcript used as compaction input."""
    lines: list[str] = []
    for e in entries:
        m = e.msg
        if m.role == "assistant":
            if m.content:
                lines.append(f"ASSISTANT: {m.content[:max_text_chars]}")
            for tc in m.tool_calls:
                args = ", ".join(f"{k}={str(v)[:600]!r}" for k, v in tc.arguments.items())
                lines.append(f"TOOL CALL {tc.name}({args})")
        elif m.role == "tool":
            body = m.content or ""
            if len(body) > max_tool_chars:
                body = body[: max_tool_chars // 2] + f"\n…[{len(body) - max_tool_chars:,} chars elided]…\n" + body[-max_tool_chars // 2 :]
            lines.append(f"TOOL RESULT ({m.name}): {body}")
        else:
            lines.append(f"USER/HARNESS: {(m.content or '')[:max_text_chars]}")
    return "\n".join(lines)
