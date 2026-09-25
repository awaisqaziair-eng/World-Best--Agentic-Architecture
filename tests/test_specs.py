"""Spec conformance: artifacts produced by a real run validate against spec/schemas/*.json.

This keeps the published specification and the implementation from drifting apart.
"""

from __future__ import annotations

import json
from pathlib import Path

from polymath.events import EventLog
from polymath.observability import load_spans
from polymath.tools import build_registry
from polymath.tools.schema import validate

from .helpers import R, TempDirTest, bash, finish

SPEC = Path(__file__).resolve().parent.parent / "spec" / "schemas"


def load_schema(name: str) -> dict:
    schema = json.loads((SPEC / name).read_text())
    defs = schema.get("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node and node["$ref"].startswith("#/$defs/"):
                return resolve(defs[node["$ref"].split("/")[-1]])
            return {k: resolve(v) for k, v in node.items() if k not in ("$schema", "$id", "$defs")}
        if isinstance(node, list):
            return [resolve(x) for x in node]
        return node

    return resolve(schema)


class TestSpecConformance(TempDirTest):
    def test_all_artifacts_conform(self):
        rt, _ = self.runtime([R("thinking", tool_calls=[("todo", {"items": [{"content": "a", "status": "in_progress"}]})]), bash("echo hi"), finish("ok", ["x"])])
        res = rt.run("do", self.ws, verify_command="true")
        sdir = rt.session_dir(res.session_id)
        ev_schema, mr, tr, rr = (load_schema(n) for n in ("event.schema.json", "model-response.schema.json", "tool-result.schema.json", "run-result.schema.json"))
        task_schema = load_schema("task.schema.json")
        events = EventLog.load(sdir / "events.jsonl")
        self.assertGreater(len(events), 8)
        for e in events:
            validate(e.to_dict(), ev_schema)
            if e.type == "model.response":
                validate(e.data["response"], mr)
            elif e.type == "tool.result":
                validate(e.data["result"], tr)
            elif e.type == "task.completed":
                validate(e.data["result"], rr)
            elif e.type == "task.submitted":
                validate(e.data["task"], task_schema)
        validate(res.to_dict(), rr)
        span_schema = load_schema("span.schema.json")
        spans = load_spans(sdir / "trace.jsonl")
        self.assertTrue(spans)
        for s in spans:
            validate(s, span_schema)

    def test_event_types_in_code_match_spec(self):
        from polymath import events as ev

        spec_types = set(load_schema("event.schema.json")["properties"]["type"]["enum"])
        self.assertEqual(spec_types, set(ev.ALL_TYPES))

    def test_tool_definitions_conform(self):
        schema = load_schema("tool-definition.schema.json")
        reg = build_registry(self.cfg(enable_web=True))
        for spec in reg.specs():
            validate(spec, schema)
            params = spec["function"]["parameters"]
            self.assertEqual(params.get("type"), "object", spec["function"]["name"])
