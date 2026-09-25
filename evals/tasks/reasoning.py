"""Math, combinatorics, constraint satisfaction and short knowledge answers."""

from __future__ import annotations

import itertools
import json
import re
from functools import lru_cache
from pathlib import Path

from ..framework import Check, EvalTask, answer_text, load_json


def digit_count(limit_digits: int = 7, target_sum: int = 30, mod: int = 7) -> int:
    @lru_cache(maxsize=None)
    def f(pos: int, s: int, r: int) -> int:
        if s > target_sum:
            return 0
        if pos == limit_digits:
            return 1 if (s == target_sum and r == 0) else 0
        return sum(f(pos + 1, s + d, (r * 10 + d) % mod) for d in range(10))

    return f(0, 0, 0)  # counts 0..9_999_999; 0 and 10^7 don't qualify


DIGITS_TRUTH = digit_count()


def digits_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    c = Check()
    p = ws / "answer.txt"
    got = p.read_text().strip().replace(",", "") if p.exists() else None
    c.eq(got, str(DIGITS_TRUTH), "answer.txt")
    c.true(str(DIGITS_TRUTH) in (res.answer or "").replace(",", ""), f"final answer should contain {DIGITS_TRUTH}")
    return c.result()


BLOCKED = {(3, 3), (4, 7), (7, 4), (8, 8), (10, 2), (2, 10), (6, 6)}


def grid_truth(n: int = 12) -> int:
    dp = [[0] * (n + 1) for _ in range(n + 1)]
    for x in range(n + 1):
        for y in range(n + 1):
            if (x, y) in BLOCKED:
                continue
            dp[x][y] = 1 if (x, y) == (0, 0) else (dp[x - 1][y] if x else 0) + (dp[x][y - 1] if y else 0)
    return dp[n][n]


GRID_TRUTH = grid_truth()


def grid_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    c = Check()
    p = ws / "answer.txt"
    c.eq(p.read_text().strip().replace(",", "") if p.exists() else None, str(GRID_TRUTH), "answer.txt")
    return c.result()


# ── Constraint satisfaction ─────────────────────────────────────────────────
TALKS = {
    "T1": {"speaker": "Ana", "audience": 150},
    "T2": {"speaker": "Ben", "audience": 40},
    "T3": {"speaker": "Ana", "audience": 55},
    "T4": {"speaker": "Chen", "audience": 180},
    "T5": {"speaker": "Dara", "audience": 30},
    "T6": {"speaker": "Ben", "audience": 120},
    "T7": {"speaker": "Eli", "audience": 60},
    "T8": {"speaker": "Chen", "audience": 25},
}
ROOMS = {"Hall": 200, "Studio": 60}
AVAIL = {"Ana": {1, 2, 3}, "Ben": {2, 3, 4}, "Chen": {1, 3, 4}, "Dara": {1, 2}, "Eli": {3, 4}}


def schedule_ok(s: dict) -> list[str]:
    errs = []
    if set(s) != set(TALKS):
        return [f"must schedule exactly {sorted(TALKS)}"]
    used = set()
    for t, v in s.items():
        room, slot = v.get("room"), v.get("slot")
        if room not in ROOMS or slot not in (1, 2, 3, 4):
            errs.append(f"{t}: invalid room/slot {v}")
            continue
        if (room, slot) in used:
            errs.append(f"{room} double-booked in slot {slot}")
        used.add((room, slot))
        if TALKS[t]["audience"] > ROOMS[room]:
            errs.append(f"{t} audience exceeds {room} capacity")
        if slot not in AVAIL[TALKS[t]["speaker"]]:
            errs.append(f"{t}: speaker unavailable in slot {slot}")
    for a, b in itertools.combinations(TALKS, 2):
        if TALKS[a]["speaker"] == TALKS[b]["speaker"] and s[a].get("slot") == s[b].get("slot"):
            errs.append(f"{a}/{b} same speaker same slot")
    if s["T3"].get("slot", 0) >= s["T6"].get("slot", 0):
        errs.append("T3 must be in an earlier slot than T6")
    if s["T4"].get("slot") == s["T1"].get("slot"):
        pass  # allowed; both need the Hall anyway (implied conflict)
    return errs


SCHED_INSTR = """Schedule 8 conference talks into 2 rooms × 4 time slots (slots 1–4). Rooms: `Hall` (capacity 200) and `Studio` (capacity 60); each room hosts at most one talk per slot.
Talks (speaker, expected audience): T1 (Ana, 150), T2 (Ben, 40), T3 (Ana, 55), T4 (Chen, 180), T5 (Dara, 30), T6 (Ben, 120), T7 (Eli, 60), T8 (Chen, 25).
Speaker availability (slots): Ana 1,2,3; Ben 2,3,4; Chen 1,3,4; Dara 1,2; Eli 3,4.
Constraints: a talk's audience must not exceed its room's capacity; a speaker cannot give two talks in the same slot; T3 must be in an earlier slot than T6.
Write `schedule.json` mapping each talk id to `{"room": <room>, "slot": <int>}`."""


def sched_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    c = Check()
    try:
        s = load_json(ws / "schedule.json")
    except Exception as e:
        c.true(False, f"schedule.json unreadable: {e}")
        return c.result()
    errs = schedule_ok(s)
    c.true(not errs, "; ".join(errs))
    return c.result()


def qa_verify(ws: Path, res, ctx) -> tuple[bool, str]:
    a = answer_text(res).replace("\\", "").replace("·", " ").replace("⋅", " ").replace("*", "")
    c = Check()
    c.true(re.search(r"n\s*log\s*\(?\s*n|nlogn|n log₂ n", a), "must state O(n log n)")
    c.true(re.search(r"not\s+(a\s+)?stable|unstable|isn['’]t\s+(a\s+)?stable|is\s+not\s+stable|non-stable", a), "must state heapsort is not stable")
    return c.result()


TASKS = [
    EvalTask("math-digit-sum", "math", "How many integers n with 1 ≤ n ≤ 10,000,000 have a digit sum of exactly 30 and are divisible by 7? Write just the number to `answer.txt` and state it in your final answer.", lambda ws: None, digits_verify, max_turns=15),
    EvalTask("math-grid-paths", "math", "A robot starts at (0, 0) and reaches (12, 12) moving only right (x+1) or up (y+1). It may never enter these blocked cells: (3,3), (4,7), (7,4), (8,8), (10,2), (2,10), (6,6). How many distinct paths are there? Write just the number to `answer.txt`.", lambda ws: None, grid_verify, max_turns=15),
    EvalTask("reason-schedule", "math", SCHED_INSTR, lambda ws: None, sched_verify, max_turns=20),
    EvalTask("qa-knowledge", "general", "What is the worst-case time complexity of heapsort, and is heapsort a stable sort? Answer briefly.", lambda ws: None, qa_verify, max_turns=6, difficulty="easy"),
]
