"""Task registry."""

from __future__ import annotations

from ..framework import EvalTask
from . import coding, data, debugging, hard, knowledge, ops, reasoning, retention

CORE: list[EvalTask] = [*coding.TASKS, *debugging.TASKS, *ops.TASKS, *data.TASKS, *reasoning.TASKS, *knowledge.TASKS]
HARD: list[EvalTask] = list(hard.TASKS)
RETENTION: list[EvalTask] = list(retention.TASKS)  # run under a small window; see retention.py
ALL: list[EvalTask] = CORE + HARD + RETENTION
BY_ID = {t.id: t for t in ALL}
assert len(BY_ID) == len(ALL), "duplicate task ids"


def select(spec: str | None) -> list[EvalTask]:
    """'core' (default, the 28-task suite), 'hard', 'all' (both), categories, or ids."""
    if not spec or spec == "core":
        return list(CORE)
    if spec == "hard":
        return list(HARD)
    if spec == "retention":
        return list(RETENTION)
    if spec == "all":
        return list(ALL)
    out: list[EvalTask] = []
    for part in spec.split(","):
        part = part.strip()
        if part in BY_ID:
            out.append(BY_ID[part])
        else:
            matched = [t for t in ALL if t.category == part or t.id.startswith(part)]
            if not matched:
                raise KeyError(f"no task or category {part!r}")
            out.extend(matched)
    seen: set[str] = set()
    return [t for t in out if not (t.id in seen or seen.add(t.id))]
