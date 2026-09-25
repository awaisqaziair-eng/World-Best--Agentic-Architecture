"""Context engine: keeps the model's working set small, relevant and cache-friendly.

Pipeline per turn (docs/05-context-engineering.md):

1. **Materialise** the view from the event log.
2. **Clear** (stage 1, cheap, lossless-ish): once the estimate crosses
   ``clear_at`` of the usable window, stub out every tool result older than the
   ``keep_recent_tool_results`` most recent ones. Clearing happens in *batches*
   and is recorded as an event, so the prompt prefix changes rarely
   (cache-shape discipline) and replay reproduces it exactly.
3. **Compact** (stage 2, lossy): past ``compact_at``, summarise everything
   before the last ``keep_recent_turns`` turns with the utility model into a
   structured summary (progress, exact facts, files, decisions, errors, next
   steps). The cut is always at a turn boundary so tool calls and results stay
   paired.
4. **Emergency** (stage 3): if still over budget, compact down to the last turn
   and middle-truncate oversized messages in the view.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from .. import events as ev
from ..events import EventLog
from ..gateway.base import ChatRequest, ModelClient
from ..types import Message
from .conversation import ConversationState, Entry, materialize, project, render_transcript, visible_entries
from .tokens import Calibrator, estimate_message, estimate_tools

SUMMARY_SYSTEM = """\
You compact the working memory of an autonomous agent that is in the middle of a task. The agent will
continue from your summary alone, so it must be complete and exact. Write a dense, structured summary:

## Task progress
What has been accomplished so far and what state the work is in.
## Key facts and values
Exact numbers, names, paths, identifiers, commands, URLs, and results discovered. Copy them verbatim.
## Files
Each file created or modified (path — purpose — current state).
## Decisions and rationale
## Problems encountered
Errors hit, their root causes, how they were resolved or whether they are still open.
## Next steps
The concrete next actions.

Rules: no chatter, no speculation, keep every detail needed to continue; at most ~1200 words."""


class ContextEngine:
    def __init__(
        self,
        *,
        window_tokens: int,
        max_output_tokens: int,
        clear_at: float = 0.45,
        compact_at: float = 0.70,
        keep_recent_tool_results: int = 8,
        keep_recent_turns: int = 6,
        clear_min_chars: int = 1200,
        summarizer: ModelClient | None = None,
        calibrator: Calibrator | None = None,
        model_name: str = "?",
        notes_reader: Callable[[], str] | None = None,
    ) -> None:
        self.window = window_tokens
        self.max_output = max_output_tokens
        self.clear_at = clear_at
        self.compact_at = compact_at
        self.keep_tools = keep_recent_tool_results
        self.keep_turns = keep_recent_turns
        self.clear_min_chars = clear_min_chars
        self.summarizer = summarizer
        self.calibrator = calibrator or Calibrator()
        self.model_name = model_name
        self.notes_reader = notes_reader
        self.last_estimate = 0

    @property
    def budget(self) -> int:
        """Usable prompt tokens (window minus reserved output)."""
        return max(4_000, self.window - self.max_output)

    def estimate(self, system: Message, msgs: list[Message], tools: list[dict[str, Any]]) -> int:
        raw = estimate_message(system) + sum(estimate_message(m) for m in msgs) + estimate_tools(tools)
        return self.calibrator.adjust(self.model_name, raw)

    # ── main entry ──────────────────────────────────────────────────────
    def prepare(self, log: EventLog, agent: str, system: Message, tools: list[dict[str, Any]], *, force_compact: bool = False) -> list[Message]:
        st = project(log.events(agent=agent), agent)
        msgs = materialize(st, clear_min_chars=self.clear_min_chars)
        est = self.estimate(system, msgs, tools)

        if (est > self.clear_at * self.budget or force_compact) and self._clear(log, agent, st, est):
            st = project(log.events(agent=agent), agent)
            msgs = materialize(st, clear_min_chars=self.clear_min_chars)
            est = self.estimate(system, msgs, tools)

        if est > self.compact_at * self.budget or force_compact:
            if self._compact(log, agent, st, est, keep_turns=self.keep_turns):
                st = project(log.events(agent=agent), agent)
                msgs = materialize(st, clear_min_chars=self.clear_min_chars)
                est = self.estimate(system, msgs, tools)

        if est > self.budget:
            if self._compact(log, agent, st, est, keep_turns=1):
                st = project(log.events(agent=agent), agent)
                msgs = materialize(st, clear_min_chars=self.clear_min_chars)
                est = self.estimate(system, msgs, tools)
            if est > self.budget:
                msgs = self._truncate_largest(msgs, est - self.budget)
                est = self.estimate(system, msgs, tools)
        self.last_estimate = est
        return msgs

    def observe_usage(self, estimated: int, actual_input_tokens: int) -> None:
        self.calibrator.observe(self.model_name, estimated, actual_input_tokens)

    # ── stage 1: clearing ───────────────────────────────────────────────
    def _clear(self, log: EventLog, agent: str, st: ConversationState, est: int) -> bool:
        tool_entries = [e for e in visible_entries(st) if e.msg.role == "tool"]
        if len(tool_entries) <= self.keep_tools:
            return False
        boundary = tool_entries[-self.keep_tools - 1].seq
        if boundary <= st.cleared_upto:
            return False
        victims = [e for e in tool_entries if st.cleared_upto < e.seq <= boundary and len(e.msg.content or "") > self.clear_min_chars]
        if not victims:
            return False
        saved = sum(len(e.msg.content or "") for e in victims) // 4
        log.append(ev.CONTEXT_CLEARED, {"upto_seq": boundary, "cleared": len(victims), "est_saved_tokens": saved, "before_tokens": est}, agent=agent)
        return True

    # ── stage 2: compaction ─────────────────────────────────────────────
    def _compact(self, log: EventLog, agent: str, st: ConversationState, est: int, *, keep_turns: int) -> bool:
        vis = visible_entries(st)
        asst_idx = [i for i, e in enumerate(vis) if e.msg.role == "assistant"]
        if len(asst_idx) <= keep_turns:
            return False
        cut = asst_idx[-keep_turns]
        to_summarize = vis[:cut]
        if not to_summarize:
            return False
        upto_seq = to_summarize[-1].seq
        t0 = time.monotonic()
        summary, method = self._summarize(st, to_summarize)
        log.append(
            ev.CONTEXT_COMPACTED,
            {
                "upto_seq": upto_seq,
                "summary": summary,
                "method": method,
                "summarized_entries": len(to_summarize),
                "before_tokens": est,
                "duration_s": round(time.monotonic() - t0, 3),
            },
            agent=agent,
        )
        return True

    def _summarize(self, st: ConversationState, entries: list[Entry]) -> tuple[str, str]:
        prev = st.compaction["summary"] if st.compaction else ""
        transcript = render_transcript(entries)
        if len(transcript) > 240_000:  # keep the summariser's own input bounded
            transcript = transcript[:120_000] + "\n…[middle of transcript elided]…\n" + transcript[-120_000:]
        plan = "\n".join(f"- [{i.get('status')}] {i.get('content')}" for i in st.plan) if st.plan else "(none)"
        notes = self.notes_reader() if self.notes_reader else ""
        task = (st.task or {}).get("instruction", "")
        user = (
            f"# Original task\n{task[:6000]}\n\n# Previous summary\n{prev or '(none)'}\n\n"
            f"# Current plan\n{plan}\n\n# Transcript to compact\n{transcript}"
        )
        tail = ""
        if st.plan:
            tail += f"\n\n## Current plan (live)\n{plan}"
        if notes.strip():
            tail += f"\n\n## Agent notes (verbatim)\n{notes[-6000:]}"
        if self.summarizer is not None:
            try:
                r = self.summarizer.complete(
                    ChatRequest(messages=[Message("system", SUMMARY_SYSTEM), Message("user", user)], max_tokens=4096, temperature=0.1, purpose="compaction")
                )
                if r.content and len(r.content.strip()) > 80:
                    return r.content.strip() + tail, "llm"
            except Exception:
                pass
        return self._fallback_summary(prev, entries) + tail, "fallback"

    @staticmethod
    def _fallback_summary(prev: str, entries: list[Entry]) -> str:
        """Deterministic, model-free digest used when the summariser fails."""
        lines = ["## Progress log (automatic digest)"]
        if prev:
            lines.append(prev[-4000:])
        for e in entries:
            m = e.msg
            if m.role == "assistant":
                if m.content:
                    lines.append(f"- Thought: {m.content[:300]}")
                for tc in m.tool_calls:
                    arg = ", ".join(f"{k}={str(v)[:160]!r}" for k, v in tc.arguments.items())
                    lines.append(f"- Called {tc.name}({arg})")
            elif m.role == "tool":
                first = (m.content or "").strip().split("\n")
                lines.append(f"  → {first[0][:200] if first else ''}{' …' if len(first) > 1 else ''}")
        return "\n".join(lines)[-16000:]

    # ── stage 3: emergency truncation ───────────────────────────────────
    def _truncate_largest(self, msgs: list[Message], excess_tokens: int) -> list[Message]:
        out = list(msgs)
        need = int(excess_tokens * 3.4) + 2000
        order = sorted(range(1, len(out)), key=lambda i: -len(out[i].content or ""))  # never touch the task (index 0)
        for i in order:
            if need <= 0:
                break
            m = out[i]
            c = m.content or ""
            if len(c) < 4000:
                continue
            keep = max(2000, len(c) - need)
            cut = len(c) - keep
            new = c[: keep // 2] + f"\n…[{cut:,} chars truncated to fit the context window]…\n" + c[-keep // 2 :]
            out[i] = Message(m.role, new, tool_calls=m.tool_calls, tool_call_id=m.tool_call_id, name=m.name, meta=m.meta)
            need -= cut
        return out
