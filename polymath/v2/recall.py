"""Recallable eviction: an enhancement tier for Pydantic AI harness compaction.

The gap it fills (docs/research/02 §3, 01 D10): the prebuilt ``ClearToolResults`` replaces old
tool results with ``[tool result cleared]``, and nothing can bring them back. The harness's
own README says reading the persisted run "does not recover what the receipt says was dropped".
The model must then re-run the tool, which costs a turn and, for anything that changed since,
returns a *different* answer. ``ToolOutputLimits(Spill)`` is lossless but only for results that
were large when produced; the moderate results that pile up over a long run are never spilled.

What this tier does, in research terms:

* **H1: addressable eviction** (ARC, arXiv 2607.25066, at eviction time). An evicted result is
  written verbatim to the SAME ``OverflowStore`` that ``ToolOutputLimits`` uses, and replaced by
  a stub that says what it was (tool, arguments, size, first and last line) and gives its handle.
  The model recovers it exactly with the prebuilt, bounded ``read_tool_result`` tool, so no new
  tool is added.
* **H3: fault-driven pinning with thrash control** (Pichay, arXiv 2603.09023). A recall of an
  evicted handle, or an identical re-read of an evicted call, is a *page fault*. The result that
  satisfied the fault is pinned for ``pin_turns``. Too many faults in a short window is
  *thrashing*, and the tier responds by keeping a larger working set.
* **H5: measure reacquisition** (arXiv 2608.16370). Every run records recalls, re-reads,
  repeated commands and redundant calls next to the tokens it saved, because a policy that
  "doesn't hurt pass rate" can still triple the work.

Batching: when triggered, eviction runs oldest-first down to a low watermark in ONE rewrite of
the history, so the prompt cache is invalidated once per episode, not once per turn (TokenPilot,
arXiv 2606.17016). Pairing is never broken: calls stay in place, and only the content of exact
``ToolReturnPart`` s changes, never typed subclasses (see the harness's ``rebuild_with_cleared``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.tools import RunContext
from pydantic_ai_harness.tool_output_limits import LocalFileStore, OverflowStore

READ_TOOL = "read_tool_result"
STUB_PREFIX = "[evicted to handle "
CLEARED_PLACEHOLDER = "[tool result cleared]"  # the prebuilt ClearToolResults placeholder, verbatim
SPILL_PREFIX = "[Tool output too large ("  # ToolOutputLimits already stored this one; its preview carries the handle
# Tools whose identical re-execution means "I lost this and am fetching it again".
READ_ONLY_TOOLS = frozenset({"read_file", "grep", "list_files", "glob", "search_files", "list_directory", "ls"})


def estimate_tokens(messages: list[ModelMessage]) -> int:
    """~4 characters per token over every part's text: deterministic and model-independent."""
    chars = 0
    for msg in messages:
        for part in msg.parts:
            content = getattr(part, "content", None)
            if content is not None:
                chars += len(content) if isinstance(content, str) else len(str(content))
            args = getattr(part, "args", None)
            if args is not None:
                chars += len(args) if isinstance(args, str) else len(json.dumps(args, default=str))
    return chars // 4


def call_signature(tool_name: str, args: Any) -> str:
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            pass
    return tool_name + ":" + json.dumps(args, sort_keys=True, default=str)


@dataclass
class RecallStats:
    """Per-run counters. ``reacquisitions`` is the number the literature says completion hides."""

    evictions: int = 0
    evicted_tokens: int = 0
    eviction_batches: int = 0
    recalls: int = 0  # read_tool_result on a handle this tier created
    re_reads: int = 0  # identical read-only call re-issued after its result was evicted
    repeat_commands: int = 0  # identical non-read-only call re-issued after eviction (ambiguous: may be legit)
    redundant_calls: int = 0  # identical read-only call while the original result is still in context
    thrash_events: int = 0
    peak_estimated_tokens: int = 0

    @property
    def reacquisitions(self) -> int:
        return self.recalls + self.re_reads

    def as_dict(self) -> dict[str, int]:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__}
        d["reacquisitions"] = self.reacquisitions
        return d


@dataclass
class _Evicted:
    handle: str
    signature: str
    tool_name: str
    faults: int = 0


@dataclass
class RecallableEviction(AbstractCapability[Any]):
    """Evict old tool results to addressable storage; track and respond to page faults."""

    context_window: int = 128_000
    trigger_fraction: float = 0.5  # evict when the estimate exceeds this fraction of the window…
    target_fraction: float = 0.3  # …down to this one (hysteresis: one batch, then quiet)
    keep_pairs: int = 4  # the most recent pairs are never evicted
    min_result_tokens: int = 250  # evicting less than ~4× the stub's size saves nothing
    pin_turns: int = 8
    thrash_window: int = 6  # turns
    thrash_faults: int = 3  # faults inside the window that count as thrashing
    store: OverflowStore = field(default_factory=LocalFileStore)
    exclude_tools: frozenset[str] = frozenset({READ_TOOL})
    addressable: bool = True  # False = control arm: identical policy, irreversible placeholder (isolates H1)
    history: list[RecallStats] = field(default_factory=list, repr=False)  # one entry per run, for evals

    # per-run state (fresh in for_run)
    stats: RecallStats = field(default_factory=RecallStats, init=False, repr=False)
    _evicted: dict[str, _Evicted] = field(default_factory=dict, init=False, repr=False)  # tool_call_id → record
    _by_handle: dict[str, str] = field(default_factory=dict, init=False, repr=False)  # handle → tool_call_id
    _evicted_sigs: dict[str, str] = field(default_factory=dict, init=False, repr=False)  # signature → tool_call_id
    _live_sigs: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _pinned_until: dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _fault_turns: list[int] = field(default_factory=list, init=False, repr=False)
    _turn: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        if not 0 < self.target_fraction < self.trigger_fraction <= 1:
            raise ValueError("need 0 < target_fraction < trigger_fraction <= 1")

    async def for_run(self, ctx: RunContext[Any]) -> RecallableEviction:
        run = replace(self)  # re-runs __post_init__ and resets every init=False field
        run.history = self.history
        self.history.append(run.stats)
        return run

    # ── observing tool traffic (faults, pins, signatures) ────────────────────
    async def after_tool_execute(self, ctx: RunContext[Any], *, call: ToolCallPart, tool_def: Any, args: Any, result: Any) -> Any:
        call_args = call.args_as_dict()
        if call.tool_name == READ_TOOL:
            origin = self._by_handle.get(str(call_args.get("handle", "")))
            if origin is not None:
                self.stats.recalls += 1
                self._fault(origin, call.tool_call_id)
            return result
        sig = call_signature(call.tool_name, call_args)
        if sig in self._evicted_sigs:
            if call.tool_name in READ_ONLY_TOOLS:
                self.stats.re_reads += 1
            else:
                self.stats.repeat_commands += 1
            self._fault(self._evicted_sigs.pop(sig), call.tool_call_id)
        elif sig in self._live_sigs and call.tool_name in READ_ONLY_TOOLS:
            self.stats.redundant_calls += 1
        self._live_sigs[sig] = call.tool_call_id
        return result

    def _fault(self, origin_call_id: str, new_call_id: str) -> None:
        rec = self._evicted.get(origin_call_id)
        if rec is not None:
            rec.faults += 1
        hot = rec is not None and rec.faults > 1
        self._pinned_until[new_call_id] = self._turn + self.pin_turns * (2 if hot else 1)
        self._fault_turns.append(self._turn)
        recent = [t for t in self._fault_turns if t > self._turn - self.thrash_window]
        self._fault_turns = recent
        if len(recent) >= self.thrash_faults:
            # Thrashing: the working set is bigger than the target leaves room for. Keep more.
            self.stats.thrash_events += 1
            self.target_fraction = min(self.trigger_fraction - 0.05, self.target_fraction + 0.1)
            self._fault_turns = []

    # ── eviction ────────────────────────────────────────────────────────────
    async def before_model_request(self, ctx: RunContext[Any], request_context: Any) -> Any:
        self._turn += 1
        messages = list(request_context.messages)
        est = estimate_tokens(messages)
        self.stats.peak_estimated_tokens = max(self.stats.peak_estimated_tokens, est)
        if est > self.trigger_fraction * self.context_window:
            request_context.messages = await self.compact(messages, ctx)
        return request_context

    async def compact(self, messages: list[ModelMessage], ctx: RunContext[Any]) -> list[ModelMessage]:
        """One batched rewrite: evict oldest eligible results until under the target watermark."""
        calls: dict[str, ToolCallPart] = {
            p.tool_call_id: p for m in messages if isinstance(m, ModelResponse) for p in m.parts if isinstance(p, ToolCallPart)
        }
        returns = [p for m in messages if isinstance(m, ModelRequest) for p in m.parts if type(p) is ToolReturnPart and p.tool_call_id in calls]
        protected = {p.tool_call_id for p in returns[len(returns) - self.keep_pairs :]} if self.keep_pairs else set()
        excess = estimate_tokens(messages) - int(self.target_fraction * self.context_window)
        replacements: dict[str, str] = {}
        for part in returns:
            if excess <= 0:
                break
            cid = part.tool_call_id
            call = calls[cid]
            text = part.content if isinstance(part.content, str) else json.dumps(part.content, default=str)
            if (
                cid in protected
                or cid in self._evicted
                or call.tool_name in self.exclude_tools
                or self._pinned_until.get(cid, -1) >= self._turn  # never-pinned must not read as pinned at turn 0
                or text.startswith((STUB_PREFIX, SPILL_PREFIX, CLEARED_PLACEHOLDER))
                or len(text) // 4 < self.min_result_tokens
            ):
                continue
            sig = call_signature(call.tool_name, call.args_as_dict())
            if self.addressable:
                # Short handle, since every stub pays for it: the run id's random TAIL (a UUIDv7's head is a
                # timestamp, so it can collide across concurrent runs) plus a per-run counter.
                handle = await self.store.write(f"ev/{(ctx.run_id or 'run')[-8:]}/{len(self._by_handle) + 1}", text.encode("utf-8"))
                stub = _stub(handle, call, text)
                self._by_handle[handle] = cid
            else:
                handle, stub = "", CLEARED_PLACEHOLDER
            self._evicted[cid] = _Evicted(handle, sig, call.tool_name)
            self._evicted_sigs[sig] = cid
            self._live_sigs.pop(sig, None)
            replacements[cid] = stub
            saved = (len(text) - len(stub)) // 4
            excess -= saved
            self.stats.evictions += 1
            self.stats.evicted_tokens += saved
        if not replacements:
            return messages
        self.stats.eviction_batches += 1
        out: list[ModelMessage] = []
        for msg in messages:
            if isinstance(msg, ModelRequest) and any(type(p) is ToolReturnPart and p.tool_call_id in replacements for p in msg.parts):
                parts = [replace(p, content=replacements[p.tool_call_id]) if type(p) is ToolReturnPart and p.tool_call_id in replacements else p for p in msg.parts]
                msg = replace(msg, parts=parts)
            out.append(msg)
        return out


def _stub(handle: str, call: ToolCallPart, text: str) -> str:
    """What the model keeps: enough to decide WHETHER to recall, and exactly how (~75 tokens)."""
    args = json.dumps(call.args_as_dict(), default=str)
    if len(args) > 120:
        args = args[:117] + "..."
    lines = text.splitlines() or [""]
    first = lines[0][:80]
    last = f" · last: {lines[-1][:80]!r}" if len(lines) > 1 else ""
    return (
        f"{STUB_PREFIX}{handle!r}: {call.tool_name}({args}) · {len(lines)} lines, {len(text):,} chars · first: {first!r}{last}."
        f" Exact text: read_tool_result(handle={handle!r}); optional pattern, offset, limit, from_end.]"
    )
