"""jsonutil, schema validation, event log, memory store, router."""

from __future__ import annotations

import json
import threading
import unittest

from polymath import jsonutil
from polymath.events import EventLog
from polymath.kernel.router import heuristic_profile
from polymath.memory import MemoryStore
from polymath.tools.schema import SchemaError, validate

from .helpers import TempDirTest


class TestLenientJSON(unittest.TestCase):
    def test_strict(self):
        self.assertEqual(jsonutil.loads_lenient('{"a": 1}'), {"a": 1})

    def test_code_fence(self):
        self.assertEqual(jsonutil.loads_lenient('```json\n{"a": [1, 2]}\n```'), {"a": [1, 2]})

    def test_surrounding_prose(self):
        self.assertEqual(jsonutil.loads_lenient('Sure! Here it is: {"ok": true} hope that helps'), {"ok": True})

    def test_trailing_comma(self):
        self.assertEqual(jsonutil.loads_lenient('{"a": 1, "b": [1,2,],}'), {"a": 1, "b": [1, 2]})

    def test_python_literals_and_single_quotes(self):
        self.assertEqual(jsonutil.loads_lenient("{'a': True, 'b': None}"), {"a": True, "b": None})

    def test_json_literals_inside_words_are_preserved(self):
        v = jsonutil.loads_lenient("{'field': 'nullable', 'flag': true}")
        self.assertEqual(v, {"field": "nullable", "flag": True})

    def test_nested_braces_in_strings(self):
        self.assertEqual(jsonutil.loads_lenient('x {"s": "a } b", "n": {"m": 1}} y'), {"s": "a } b", "n": {"m": 1}})

    def test_expect_type(self):
        with self.assertRaises(jsonutil.JSONRepairError):
            jsonutil.loads_lenient("[1,2]", expect=dict)

    def test_garbage_raises(self):
        with self.assertRaises(jsonutil.JSONRepairError):
            jsonutil.loads_lenient("definitely not json")

    def test_digest_is_order_independent(self):
        self.assertEqual(jsonutil.digest({"a": 1, "b": 2}), jsonutil.digest({"b": 2, "a": 1}))


class TestSchema(unittest.TestCase):
    S = {
        "type": "object",
        "properties": {
            "n": {"type": "integer", "minimum": 1},
            "flag": {"type": "boolean", "default": False},
            "mode": {"type": "string", "enum": ["a", "b"]},
            "items": {"type": "array", "items": {"type": "object", "properties": {"s": {"type": "string"}}, "required": ["s"]}},
        },
        "required": ["n"],
    }

    def test_coercions_recorded(self):
        notes: list[str] = []
        v = validate({"n": "5", "flag": "true"}, self.S, notes=notes)
        self.assertEqual(v, {"n": 5, "flag": True})
        self.assertEqual(len(notes), 2)

    def test_default_applied(self):
        self.assertEqual(validate({"n": 1}, self.S)["flag"], False)

    def test_errors_collected_with_paths(self):
        with self.assertRaises(SchemaError) as cm:
            validate({"n": 0, "mode": "c", "items": [{"s": "x"}, {}]}, self.S)
        msg = str(cm.exception)
        self.assertIn("$.n: must be >= 1", msg)
        self.assertIn("$.mode: must be one of", msg)
        self.assertIn("$.items[1].s: required", msg)

    def test_missing_required(self):
        with self.assertRaises(SchemaError):
            validate({}, self.S)

    def test_string_to_array_coercion(self):
        v = validate({"n": 1, "items": '[{"s": "q"}]'}, self.S)
        self.assertEqual(v["items"], [{"s": "q"}])

    def test_additional_properties_false(self):
        with self.assertRaises(SchemaError):
            validate({"x": 1}, {"type": "object", "properties": {}, "additionalProperties": False})

    def test_bool_is_not_integer(self):
        with self.assertRaises(SchemaError):
            validate({"n": True}, self.S)


class TestEventLog(TempDirTest):
    def test_append_reload(self):
        p = self.tmp / "s1" / "events.jsonl"
        with EventLog(p) as log:
            log.append("a.b", {"x": 1})
            log.append("c.d", {"y": 2}, agent="sub")
        evs = EventLog.load(p)
        self.assertEqual([e.seq for e in evs], [1, 2])
        self.assertEqual(evs[1].agent, "sub")
        with EventLog(p) as log2:
            self.assertEqual(log2.append("e.f").seq, 3)

    def test_torn_tail_is_repaired(self):
        p = self.tmp / "s2" / "events.jsonl"
        with EventLog(p) as log:
            log.append("a.b", {"x": 1})
        with open(p, "a") as fh:
            fh.write('{"seq": 2, "ts": 1, "type": "tor')  # crash mid-write
        with EventLog(p) as log:
            self.assertEqual(len(log), 1)
            log.append("c.d")
        self.assertEqual([e.seq for e in EventLog.load(p)], [1, 2])

    def test_corrupt_middle_line_raises(self):
        p = self.tmp / "s3" / "events.jsonl"
        p.parent.mkdir(parents=True)
        p.write_text('{"seq":1,"ts":1,"type":"a","agent":"main","data":{}}\nGARBAGE\n{"seq":2,"ts":1,"type":"b","agent":"main","data":{}}\n')
        with self.assertRaises(ValueError):
            EventLog.load(p)

    def test_concurrent_appends_are_dense_and_whole(self):
        p = self.tmp / "s4" / "events.jsonl"
        log = EventLog(p)

        def worker(k: int) -> None:
            for i in range(250):
                log.append("t.x", {"k": k, "i": i, "pad": "x" * (i % 50)}, agent=f"a{k}")

        ts = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        log.close()
        evs = EventLog.load(p)
        self.assertEqual([e.seq for e in evs], list(range(1, 2001)))

    def test_listener_errors_do_not_break_append(self):
        log = EventLog(self.tmp / "s5" / "events.jsonl")
        log.subscribe(lambda e: 1 / 0)
        self.assertEqual(log.append("x.y").seq, 1)
        log.close()


class TestMemory(TempDirTest):
    def test_bm25_ranking_and_delete(self):
        m = MemoryStore(self.tmp / "mem.jsonl")
        a = m.save("The user prefers tabs over spaces in Python files", ["preference", "python"])
        m.save("Deploys go through the staging cluster first")
        m.save("The billing service database is Postgres 15")
        hits = m.search("python indentation preference")
        self.assertEqual(hits[0][1].id, a.id)
        self.assertTrue(m.delete(a.id))
        self.assertFalse(any(r.id == a.id for r in m.all()))
        self.assertEqual(len(MemoryStore(self.tmp / "mem.jsonl").all()), 2)

    def test_empty_query_returns_recent(self):
        m = MemoryStore(self.tmp / "mem2.jsonl")
        m.save("one")
        m.save("two")
        self.assertEqual(m.search("", k=1)[0][1].content, "two")


class TestRouter(unittest.TestCase):
    def test_categories(self):
        self.assertEqual(heuristic_profile("Fix the failing test in parser.py, it raises an exception").category, "debugging")
        self.assertEqual(heuristic_profile("Compute total revenue per region from sales.csv").category, "data")
        self.assertEqual(heuristic_profile("Create a branch, commit, merge and tag v1 in git").category, "git")
        self.assertIn("data-analysis", heuristic_profile("aggregate the csv columns").skills)

    def test_identifiers_do_not_trigger_keywords(self):
        # Regression: v1 routed this to "git" because `merge_intervals` contains "merge".
        p = heuristic_profile(
            "In the workspace, create `intervals.py` with a function `merge_intervals(intervals)` that returns merged "
            "intervals. Also create `test_intervals.py` with unittest tests; all must pass."
        )
        self.assertEqual(p.category, "coding")
        self.assertNotIn("git-workflow", p.skills)

    def test_git_needs_real_evidence(self):
        self.assertNotEqual(heuristic_profile("Tag each row of the CSV with its region").category, "git")
        self.assertEqual(heuristic_profile("In this git repo create a branch and merge it").category, "git")

    def test_complexity(self):
        self.assertEqual(heuristic_profile("What is 2+2?").complexity, "low")
        long = "\n".join(f"- requirement {i}" for i in range(8))
        self.assertEqual(heuristic_profile("Build this:\n" + long).complexity, "high")


if __name__ == "__main__":
    unittest.main()
