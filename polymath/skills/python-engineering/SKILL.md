---
name: python-engineering
description: Writing, structuring, running and testing Python code (modules, CLIs, packages, unit tests, performance).
---
## Workflow
1. Inspect before writing: `ls`, read existing modules/tests, check `python3 --version` and what is importable.
2. Design the public interface first (function signatures, CLI arguments, file formats) exactly as the task specifies — names, argument order, return types and output formats are part of the contract.
3. Implement in small, testable functions. Standard library first; only `pip install` if truly needed (and check it works).
4. Write tests with `unittest` (always available) unless pytest is present and preferred:
   `python3 -m unittest discover -s . -p 'test_*.py' -v`
5. Run the code for real. Exercise edge cases: empty input, one element, duplicates, negative numbers, unicode, very large input.
6. Re-read the task statement and check every requirement against what you built before finishing.

## Conventions
- `if __name__ == "__main__":` for scripts; `argparse` for CLIs (gives `--help` for free); exit code 0 on success, non-zero on error; errors to stderr.
- Persist JSON with `json.dump(obj, f, indent=2)`; read with an existence check.
- Performance: pick the right data structure (dict/set O(1) lookups, `collections.OrderedDict`/`deque`, `heapq`, `bisect`); measure with `time.perf_counter()`.
- Never leave debug prints in deliverables. Keep files importable (no side effects at import time).
