---
name: debugging
description: Systematically finding and fixing bugs, failing tests, crashes and wrong outputs.
---
## Method (do not skip steps)
1. **Reproduce**: run the failing test/program and capture the exact error. `python3 -m unittest -v 2>&1 | tail -50`.
2. **Localise**: read the traceback bottom-up; open the implicated code with read_file; grep for the symbol's definition and all its call sites.
3. **Hypothesise**: state the root cause in one sentence *before* editing. Check it with a quick experiment (print/assert in a scratch script, `python3 -c`).
4. **Fix the cause, not the symptom**: minimal, targeted edit. Never weaken or edit tests to make them pass unless the task says the test is wrong.
5. **Verify**: re-run the originally failing tests AND the full suite (regressions). If there are several bugs, iterate until everything is green.
6. Report what the root cause was and how you verified the fix.

## Common root causes
off-by-one in ranges/slicing/pagination; mutable default arguments; integer vs float division and rounding (`round()` is banker's rounding — use `decimal` for money); shallow copies; mutating a list while iterating; timezone-naive datetimes; string/bytes confusion; wrong operator precedence; unhandled None/empty input; stale state between calls.
