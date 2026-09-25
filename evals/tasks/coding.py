"""Coding: implement, test, build a CLI project, optimise."""

from __future__ import annotations

import json
import random
import re
import shutil
import tempfile
from pathlib import Path

from ..framework import Check, EvalTask, run, run_py, write

# ── LRU cache ───────────────────────────────────────────────────────────────
LRU_INSTR = """Create `lru_cache.py` in the workspace containing a class `LRUCache` implementing a least-recently-used cache:
- `LRUCache(capacity)` — capacity is a positive integer.
- `get(key)` returns the value stored for `key`, or `-1` if the key is absent. A successful `get` marks the key as most recently used.
- `put(key, value)` inserts or updates the value; an update also marks the key as most recently used. When inserting a new key into a full cache, first evict the least recently used key.
Both operations must run in O(1) average time. Test your implementation before finishing."""

LRU_VERIFY = r"""
import time
from lru_cache import LRUCache
c = LRUCache(2); c.put(1, 1); c.put(2, 2)
assert c.get(1) == 1
c.put(3, 3)
assert c.get(2) == -1, "2 should be evicted"
c.put(4, 4)
assert c.get(1) == -1, "1 should be evicted"
assert c.get(3) == 3 and c.get(4) == 4
c = LRUCache(1); c.put(1, 1); c.put(2, 2)
assert c.get(1) == -1 and c.get(2) == 2
c = LRUCache(2); c.put(1, 1); c.put(2, 2); c.put(1, 10); c.put(3, 3)
assert c.get(1) == 10 and c.get(2) == -1, "update must refresh recency"
c = LRUCache(2); c.put("a", None) if False else None
c = LRUCache(1000); t = time.perf_counter()
for i in range(200000):
    c.put(i % 1500, i); c.get((i * 7) % 1500)
el = time.perf_counter() - t
assert el < 3.0, f"too slow: {el:.2f}s for 400k ops"
print("OK")
"""


def lru_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    rc, out, err = run_py(ws, LRU_VERIFY)
    return rc == 0 and "OK" in out, (out + err)[-600:]


# ── Interval merge + own tests ──────────────────────────────────────────────
INT_INSTR = """In the workspace, create `intervals.py` with a function `merge_intervals(intervals)` that takes a list of `[start, end]` integer pairs (start <= end, in any order) and returns a new list of merged, non-overlapping intervals sorted by start. Intervals that overlap or touch (e.g. `[1, 3]` and `[3, 5]`) must be merged. The input list must not be modified.
Also create `test_intervals.py` with at least 5 meaningful `unittest` test methods covering edge cases; all of them must pass with `python3 -m unittest test_intervals`."""

INT_VERIFY = r"""
import copy
from intervals import merge_intervals as m
def norm(x): return [list(i) for i in x]
cases = [([], []), ([[1,3],[2,6],[8,10],[15,18]], [[1,6],[8,10],[15,18]]), ([[1,4],[4,5]], [[1,5]]),
         ([[5,6],[1,2]], [[1,2],[5,6]]), ([[1,10],[2,3],[4,5]], [[1,10]]), ([[1,1]], [[1,1]]),
         ([[-5,-1],[-2,3]], [[-5,3]]), ([[1,2],[3,4]], [[1,2],[3,4]]), ([[2,3],[1,2],[3,3]], [[1,3]])]
for inp, want in cases:
    before = copy.deepcopy(inp)
    got = norm(m(inp))
    assert got == want, f"merge_intervals({before}) = {got}, want {want}"
    assert inp == before, "input was mutated"
print("OK")
"""


def int_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    c = Check()
    rc, out, err = run_py(ws, INT_VERIFY)
    c.true(rc == 0 and "OK" in out, f"hidden tests: {(out + err)[-300:]}")
    rc2, out2, err2 = run("python3 -m unittest test_intervals -v", ws)
    c.true(rc2 == 0, f"agent tests fail: {err2[-300:]}")
    m = re.search(r"Ran (\d+) test", err2 + out2)
    c.true(m and int(m.group(1)) >= 5, f"needs >=5 tests, found {m.group(1) if m else 0}")
    return c.result()


# ── CLI todo project ────────────────────────────────────────────────────────
TODO_INSTR = """Build a command-line todo manager `todo.py` (Python 3, standard library only) that stores its data in `todos.json` in the current working directory. Commands:
- `python3 todo.py add "<text>"` → prints `Added #<id>`
- `python3 todo.py list` → prints one line per todo in id order: `<id>. [ ] <text>`, or `<id>. [x] <text>` when done; prints `No todos.` if there are none
- `python3 todo.py done <id>` → marks it done and prints `Completed #<id>`
- `python3 todo.py remove <id>` → deletes it and prints `Removed #<id>`
IDs start at 1 and increase by one for each added todo; IDs of removed todos are never reused. An unknown id must print an error message to stderr and exit with status 1. `todos.json` may not exist initially. Include unit tests in `test_todo.py`."""


def todo_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    c = Check()
    if not c.true((ws / "todo.py").exists(), "todo.py missing"):
        return c.result()
    c.true((ws / "test_todo.py").exists(), "test_todo.py missing")
    d = Path(tempfile.mkdtemp())
    shutil.copy(ws / "todo.py", d / "todo.py")
    steps = [
        (["list"], 0, "No todos."),
        (["add", "Buy milk"], 0, "Added #1"),
        (["add", "Walk the dog"], 0, "Added #2"),
        (["list"], 0, "1. [ ] Buy milk\n2. [ ] Walk the dog"),
        (["done", "1"], 0, "Completed #1"),
        (["list"], 0, "1. [x] Buy milk\n2. [ ] Walk the dog"),
        (["remove", "2"], 0, "Removed #2"),
        (["add", "Read"], 0, "Added #3"),
        (["list"], 0, "1. [x] Buy milk\n3. [ ] Read"),
        (["done", "99"], 1, None),
        (["remove", "42"], 1, None),
    ]
    for args, want_rc, want_out in steps:
        rc, out, err = run(["python3", "todo.py", *args], d, timeout=20)
        c.eq(rc, want_rc, f"`todo.py {' '.join(args)}` exit code")
        if want_out is not None:
            c.eq(out.strip(), want_out, f"`todo.py {' '.join(args)}` output")
        else:
            c.true(err.strip() != "", f"`todo.py {' '.join(args)}` must print an error to stderr")
    shutil.rmtree(d, ignore_errors=True)
    return c.result()


# ── Performance optimisation ────────────────────────────────────────────────
SLOW = '''def count_pairs(nums, target):
    """Return the number of index pairs (i, j) with i < j and nums[i] + nums[j] == target."""
    count = 0
    for i in range(len(nums)):
        for j in range(i + 1, len(nums)):
            if nums[i] + nums[j] == target:
                count += 1
    return count
'''
BENCH = '''import random, time
from slow import count_pairs
rng = random.Random(1)
nums = [rng.randint(-1000, 1000) for _ in range(200_000)]
t = time.perf_counter()
print("pairs:", count_pairs(nums, 17))
print(f"elapsed: {time.perf_counter() - t:.3f}s")
'''
OPT_INSTR = """`slow.py` contains `count_pairs(nums, target)`, which is far too slow: `python3 bench.py` would take hours. Optimise `count_pairs` so that `python3 bench.py` finishes in under 2 seconds, keeping the function name, signature and exact results (including inputs with duplicates, negative numbers and target 0). Report the number of pairs the benchmark prints."""

OPT_VERIFY = r"""
import random, time
from slow import count_pairs
def ref(nums, t):
    return sum(1 for i in range(len(nums)) for j in range(i+1, len(nums)) if nums[i]+nums[j]==t)
rng = random.Random(99)
for k in range(300):
    n = rng.randint(0, 40); nums = [rng.randint(-6, 6) for _ in range(n)]; t = rng.randint(-8, 8)
    assert count_pairs(list(nums), t) == ref(nums, t), (nums, t)
assert count_pairs([0,0,0,0], 0) == 6 and count_pairs([], 3) == 0 and count_pairs([5], 10) == 0
rng = random.Random(1); nums = [rng.randint(-1000, 1000) for _ in range(200_000)]
t0 = time.perf_counter(); r = count_pairs(nums, 17); el = time.perf_counter() - t0
assert el < 3.0, f"too slow: {el:.2f}s"
print("OK", r)
"""


def opt_setup(ws: Path) -> None:
    write(ws, "slow.py", SLOW)
    write(ws, "bench.py", BENCH)


def opt_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    c = Check()
    rc, out, err = run_py(ws, OPT_VERIFY, timeout=60)
    c.true(rc == 0 and out.startswith("OK"), f"hidden: {(out + err)[-400:]}")
    if rc == 0:
        truth = out.split()[1]
        c.true(truth in (res.answer or "").replace(",", ""), f"final answer should report {truth} pairs")
    return c.result()


TASKS = [
    EvalTask("code-lru-cache", "coding", LRU_INSTR, lambda ws: None, lru_verify, max_turns=25),
    EvalTask("code-intervals-tests", "coding", INT_INSTR, lambda ws: None, int_verify, max_turns=25),
    EvalTask("code-cli-todo", "coding", TODO_INSTR, lambda ws: None, todo_verify, max_turns=40, difficulty="hard"),
    EvalTask("code-optimize", "coding", OPT_INSTR, opt_setup, opt_verify, max_turns=25),
]
