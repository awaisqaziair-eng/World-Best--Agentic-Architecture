"""Span tracer following the OpenTelemetry GenAI semantic conventions.

Spans are written as OTLP-shaped JSON lines to ``<session>/trace.jsonl``:

* ``invoke_agent <agent_id>``  — one per agent run (root, or child of a delegate tool span)
* ``chat <model>``             — one per model call
* ``execute_tool <tool>``      — one per tool call

Attribute names use the ``gen_ai.*`` namespace (``gen_ai.operation.name``,
``gen_ai.request.model``, ``gen_ai.usage.input_tokens``, ``gen_ai.tool.name``,
``gen_ai.tool.call.id``, ``gen_ai.conversation.id`` …) so the file can be shipped
to any OTel-compatible backend with a trivial converter. No OTel SDK needed.
"""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


def _rand_hex(n_bytes: int) -> str:
    return os.urandom(n_bytes).hex()


@dataclass
class Span:
    name: str
    trace_id: str
    span_id: str
    parent_id: str | None
    start_ns: int
    end_ns: int | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "UNSET"
    status_message: str = ""

    def set(self, key: str, value: Any) -> "Span":
        if value is not None:
            self.attributes[key] = value
        return self

    def update(self, attrs: dict[str, Any]) -> "Span":
        for k, v in attrs.items():
            self.set(k, v)
        return self

    def fail(self, message: str) -> None:
        self.status = "ERROR"
        self.status_message = message[:500]

    def to_otlp(self) -> dict[str, Any]:
        return {
            "traceId": self.trace_id,
            "spanId": self.span_id,
            "parentSpanId": self.parent_id or "",
            "name": self.name,
            "startTimeUnixNano": self.start_ns,
            "endTimeUnixNano": self.end_ns,
            "attributes": self.attributes,
            "status": {"code": self.status if self.status != "UNSET" else "OK", "message": self.status_message},
        }


class Tracer:
    def __init__(self, path: str | Path | None, *, trace_id: str | None = None) -> None:
        self.path = Path(path) if path else None
        self.trace_id = trace_id or _rand_hex(16)
        self._lock = threading.Lock()
        self._local = threading.local()

    def _stack(self) -> list[Span]:
        if not hasattr(self._local, "stack"):
            self._local.stack = []
        return self._local.stack

    def current(self) -> Span | None:
        st = self._stack()
        return st[-1] if st else None

    @contextmanager
    def span(self, name: str, attributes: dict[str, Any] | None = None, *, parent: Span | None = None) -> Iterator[Span]:
        par = parent or self.current()
        sp = Span(name, self.trace_id, _rand_hex(8), par.span_id if par else None, time.time_ns(), attributes=dict(attributes or {}))
        self._stack().append(sp)
        try:
            yield sp
        except BaseException as e:
            sp.fail(f"{type(e).__name__}: {e}")
            raise
        finally:
            self._stack().pop()
            sp.end_ns = time.time_ns()
            self._export(sp)

    def record(self, name: str, start_ns: int, end_ns: int, attributes: dict[str, Any], *, parent: Span | None = None, error: str | None = None) -> Span:
        par = parent or self.current()
        sp = Span(name, self.trace_id, _rand_hex(8), par.span_id if par else None, start_ns, end_ns, dict(attributes))
        if error:
            sp.fail(error)
        self._export(sp)
        return sp

    def _export(self, sp: Span) -> None:
        if self.path is None:
            return
        line = json.dumps(sp.to_otlp(), ensure_ascii=False, default=str)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")


def load_spans(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def render_tree(spans: list[dict[str, Any]]) -> str:
    """ASCII waterfall of a trace: indentation = nesting, with duration and tokens."""
    by_parent: dict[str, list[dict[str, Any]]] = {}
    for s in spans:
        by_parent.setdefault(s.get("parentSpanId") or "", []).append(s)
    for v in by_parent.values():
        v.sort(key=lambda s: s["startTimeUnixNano"])
    if not spans:
        return "(no spans)"
    t0 = min(s["startTimeUnixNano"] for s in spans)
    lines: list[str] = []

    def walk(pid: str, depth: int) -> None:
        for s in by_parent.get(pid, []):
            a = s.get("attributes", {})
            dur = ((s.get("endTimeUnixNano") or s["startTimeUnixNano"]) - s["startTimeUnixNano"]) / 1e9
            off = (s["startTimeUnixNano"] - t0) / 1e9
            extra = ""
            if "gen_ai.usage.input_tokens" in a:
                extra = f" in={a['gen_ai.usage.input_tokens']:,} out={a.get('gen_ai.usage.output_tokens', 0):,}"
            err = " ✗" if s.get("status", {}).get("code") == "ERROR" else ""
            lines.append(f"{off:8.2f}s {'  ' * depth}{s['name']} [{dur:.2f}s]{extra}{err}")
            walk(s["spanId"], depth + 1)

    walk("", 0)
    # Orphans: spans whose parent never ended (e.g. the process was killed mid-run).
    known = {s["spanId"] for s in spans}
    for pid in sorted(p for p in by_parent if p and p not in known):
        lines.append(f"{'':>9} (spans whose parent {pid[:8]}… was not recorded)")
        walk(pid, 1)
    return "\n".join(lines)
