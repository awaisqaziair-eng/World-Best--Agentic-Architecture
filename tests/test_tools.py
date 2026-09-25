"""Terminal, file, search, planning tools and the registry's edge handling."""

from __future__ import annotations

import time
import unittest

from polymath.config import Config
from polymath.tools import ToolContext, build_registry
from polymath.tools.search import GrepTool
from polymath.tools.terminal import PersistentShell
from polymath.types import ToolCall

from .helpers import TempDirTest


class TestPersistentShell(TempDirTest):
    def setUp(self):
        super().setUp()
        self.sh = PersistentShell(self.ws, scratch_dir=self.tmp / "shs")

    def tearDown(self):
        self.sh.close()
        super().tearDown()

    def test_state_persists(self):
        self.sh.run("mkdir -p a/b && cd a/b && export FOO=bar && f(){ echo fn-$1; }")
        r = self.sh.run("pwd; echo $FOO; f x")
        self.assertEqual(r.output.split("\n")[:3], [str(self.ws / "a" / "b"), "bar", "fn-x"])
        self.assertEqual(r.cwd, str(self.ws / "a" / "b"))

    def test_heredoc_and_multiline(self):
        r = self.sh.run("python3 - <<'PY'\nprint(sum(range(10)))\nPY\necho 'q\"uote'")
        self.assertEqual(r.output.strip().split("\n"), ["45", 'q"uote'])

    def test_syntax_error_is_contained(self):
        r = self.sh.run("echo 'unclosed")
        self.assertNotEqual(r.exit_code, 0)
        self.assertEqual(self.sh.run("echo alive").output.strip(), "alive")

    def test_stdin_is_devnull(self):
        r = self.sh.run("read x; echo got=[$x]", timeout=5)
        self.assertEqual(r.output.strip(), "got=[]")
        self.assertFalse(r.timed_out)

    def test_exit_code_and_stderr_merged(self):
        r = self.sh.run("echo out; echo err 1>&2; exit_code_test(){ return 3; }; exit_code_test")
        self.assertEqual(r.exit_code, 3)
        self.assertIn("err", r.output)

    def test_progress_bar_collapsed(self):
        r = self.sh.run("printf '10%%\\r50%%\\r100%%\\ndone\\n'")
        self.assertEqual(r.output, "100%\ndone\n")

    def test_timeout_spares_background_processes(self):
        self.sh.run("sleep 60 > /dev/null 2>&1 & echo $! > bg.pid")
        bg = int((self.ws / "bg.pid").read_text())
        t0 = time.monotonic()
        r = self.sh.run("sleep 30", timeout=1.0)
        self.assertTrue(r.timed_out)
        self.assertLess(time.monotonic() - t0, 6)
        self.assertEqual(self.sh.run(f"kill -0 {bg} && echo bg-alive").output.strip(), "bg-alive")
        self.sh.run(f"kill {bg}")

    def test_exit_restarts_shell_in_same_cwd(self):
        self.sh.run("mkdir -p d && cd d")
        r = self.sh.run("echo bye; exit 7")
        self.assertTrue(r.shell_died)
        self.assertEqual(r.exit_code, 7)
        self.assertEqual(self.sh.run("pwd").output.strip(), str(self.ws / "d"))

    def test_huge_output_is_bounded(self):
        r = self.sh.run("yes abcdefghij | head -c 20000000; echo END", timeout=60)
        self.assertLess(len(r.output), 5_000_000)
        self.assertGreater(r.dropped_bytes, 0)
        self.assertTrue(r.output.rstrip().endswith("END"))

    def test_unresponsive_shell_is_replaced(self):
        r = self.sh.run("while true; do :; done", timeout=0.5)
        self.assertTrue(r.timed_out and r.restarted)
        # No child process exists to signal, so the escalation must not wait out its full 7 s of grace.
        self.assertLess(r.duration_s, 3.0)
        self.assertEqual(self.sh.run("echo ok").output.strip(), "ok")

    def test_timeout_still_escalates_against_children(self):
        # A child that ignores SIGINT must still be stopped (by SIGTERM) and the shell kept.
        r = self.sh.run("trap '' INT; sleep 30", timeout=0.5)
        self.assertTrue(r.timed_out)
        self.assertEqual(self.sh.run("echo ok").output.strip(), "ok")


class ToolTestBase(TempDirTest):
    def setUp(self):
        super().setUp()
        self.cfg_ = Config().replace(home=str(self.tmp / "home"), max_tool_output_chars=2000, enable_web=False)
        self.reg = build_registry(self.cfg_)
        self.ctx = ToolContext(self.ws, self.tmp / "sess", "sess", "main", self.cfg_)
        self.n = 0

    def tearDown(self):
        self.ctx.close()
        super().tearDown()

    def call(self, name, **args):
        self.n += 1
        return self.reg.execute(ToolCall(f"c{self.n}", name, args), self.ctx)


class TestFileTools(ToolTestBase):
    def test_write_read_paging(self):
        self.call("write_file", path="f.txt", content="\n".join(f"line {i}" for i in range(1, 51)) + "\n")
        r = self.call("read_file", path="f.txt", offset=10, limit=3)
        self.assertIn("    10\tline 10", r.output)
        self.assertIn("showing lines 10-12 of 50", r.output)

    def test_read_missing_suggests(self):
        self.call("write_file", path="config.yaml", content="a: 1")
        r = self.call("read_file", path="confg.yaml")
        self.assertTrue(r.is_error)
        self.assertIn("config.yaml", r.output)

    def test_read_binary_and_dir(self):
        (self.ws / "b.bin").write_bytes(b"\x00\x01\x02" * 100)
        self.assertIn("binary file", self.call("read_file", path="b.bin").output)
        self.assertIn("b.bin", self.call("read_file", path=".").output)

    def test_edit_unique_multiple_and_hint(self):
        self.call("write_file", path="m.py", content="def f():\n    return 1\n\ndef g():\n    return 1\n")
        amb = self.call("edit_file", path="m.py", old_string="return 1", new_string="return 2")
        self.assertTrue(amb.is_error)
        self.assertIn("occurs 2 times", amb.output)
        ok = self.call("edit_file", path="m.py", old_string="def g():\n    return 1", new_string="def g():\n    return 2")
        self.assertFalse(ok.is_error, ok.output)
        miss = self.call("edit_file", path="m.py", old_string="def  f():\n  return 1", new_string="x")
        self.assertTrue(miss.is_error)
        self.assertIn("most similar region", miss.output)
        allr = self.call("edit_file", path="m.py", old_string="return", new_string="yield", replace_all=True)
        self.assertFalse(allr.is_error)
        self.assertEqual((self.ws / "m.py").read_text().count("yield"), 2)

    def test_glob_and_grep(self):
        (self.ws / "src" / "pkg").mkdir(parents=True)
        (self.ws / "src" / "pkg" / "a.py").write_text("def alpha():\n    pass\n")
        (self.ws / "src" / "b.txt").write_text("alpha beta\n")
        (self.ws / "node_modules").mkdir()
        (self.ws / "node_modules" / "x.py").write_text("def alpha(): pass\n")
        g = self.call("glob", pattern="**/*.py")
        self.assertIn("src/pkg/a.py", g.output)
        self.assertNotIn("node_modules", g.output)
        r = self.call("grep", pattern=r"def alpha", glob="*.py")
        self.assertIn("src/pkg/a.py:1:def alpha", r.output)
        self.assertNotIn("node_modules", r.output)
        py = GrepTool()._py({"pattern": "alpha", "context": 0}, self.ws, 50)  # fallback path
        self.assertIn("a.py", py.text)
        self.assertNotIn("node_modules", py.text)

    def test_output_is_clipped_and_spilled(self):
        r = self.call("bash", command="python3 -c \"print('x'*50000)\"")
        self.assertTrue(r.truncated)
        self.assertLessEqual(len(r.output), 2600)
        self.assertIn("full output saved to", r.output)


class TestRegistryEdges(ToolTestBase):
    def test_unknown_tool(self):
        r = self.call("does_not_exist")
        self.assertTrue(r.is_error)
        self.assertIn("Available tools", r.output)

    def test_parse_error_passthrough(self):
        r = self.reg.execute(ToolCall("z", "bash", {}, raw_arguments="{bad", parse_error="Expecting value"), self.ctx)
        self.assertTrue(r.is_error)
        self.assertIn("not valid JSON", r.output)

    def test_schema_error(self):
        r = self.call("read_file", offset=3)
        self.assertTrue(r.is_error)
        self.assertIn("$.path: required", r.output)

    def test_crashing_tool_is_captured(self):
        from polymath.tools.base import Tool

        class Boom(Tool):
            name = "boom"

            def run(self, args, ctx):
                raise RuntimeError("kaboom")

        self.reg.register(Boom())
        r = self.call("boom")
        self.assertTrue(r.is_error)
        self.assertIn("kaboom", r.output)

    def test_parallel_batch_preserves_order(self):
        for i in range(6):
            (self.ws / f"f{i}.txt").write_text(f"content {i}\n")
        calls = [ToolCall(f"p{i}", "read_file", {"path": f"f{i}.txt"}) for i in range(6)]
        calls.insert(3, ToolCall("w", "write_file", {"path": "new.txt", "content": "x"}))
        res = self.reg.execute_batch(calls, self.ctx)
        self.assertEqual([r.call_id for r in res], [c.id for c in calls])
        self.assertIn("content 5", res[-1].output)

    def test_todo_and_notes(self):
        r = self.call("todo", items=[{"content": "a", "status": "completed"}, {"content": "b", "status": "in_progress"}])
        self.assertIn("(1/2 done)", r.output)
        self.call("notes", action="append", content="fact 1")
        self.call("notes", action="append", content="fact 2")
        self.assertEqual(self.call("notes", action="read").output.strip(), "fact 1\nfact 2")

    def test_finish_sets_state(self):
        self.call("finish", answer="42", artifacts=["out.txt"])
        self.assertEqual(self.ctx.state["finish"]["answer"], "42")

    def test_ask_user_without_human(self):
        self.assertIn("No human is available", self.call("ask_user", question="which?").output)


if __name__ == "__main__":
    unittest.main()
