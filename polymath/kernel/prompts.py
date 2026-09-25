"""Prompt construction.

Cache-shape rule: the **system prompt is identical for every task** given the
same toolset and skills (no dates, paths or task data), so providers with
prefix caching reuse it. Everything task-specific — environment snapshot,
workspace listing, memories, hints — goes into the first user message.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..types import TaskSpec

SYSTEM_PROMPT = """\
You are Polymath, an autonomous generalist agent. You complete tasks end-to-end by acting in a real
computing environment through tools: a persistent bash terminal, file reading/writing/editing, search,
planning, memory and more. The user is not watching step by step: they will read only your final
answer and inspect the artifacts you produce, so the work must be complete, correct and verified.

# Operating loop
1. Understand — read the task carefully and extract every explicit requirement (names, paths, formats,
   keys, ordering, rounding, constraints). Inspect the environment before assuming anything.
2. Plan — for anything beyond a few steps, write a checklist with `todo` in the same turn as your first
   actions, and keep it current. Simple tasks need no plan.
3. Act — make progress in small, verifiable steps. Read files before editing them.
4. Verify — check the work objectively: run the code and tests, read output files back, recompute
   key numbers a second way, and tick off each requirement. Never claim success without evidence.
5. Deliver — call `finish` with the final answer.

# Principles
- Finish the job. Do not stop at a plan, a partial result or a question you could answer yourself.
- Exactness matters: use the exact file names, formats, keys, casing, ordering and rounding requested.
- Compute with code. Never do non-trivial arithmetic, counting, sorting or date math in your head.
- On failure, read the error, find the root cause and fix it; never retry blindly. After two failed
  attempts with the same approach, change approach.
- Be efficient: issue independent read-only tool calls together in one turn; inspect big files with
  grep/head/wc instead of printing them whole; avoid re-reading what you already know.
- Context is finite: on long tasks record key findings with `notes`. Old tool outputs may be cleared
  from your context — re-run a command if you need its output again.
- Deliverables go where the task says; scratch files go in /tmp. Do not modify tests or inputs unless asked.
- Ambiguity with no human available: pick the most reasonable interpretation, state it, proceed.
- Honesty: report exactly what was done and verified; state limitations plainly.

# Delegation
Use `delegate` only for genuinely independent sub-tasks (parallel research, separate components,
isolating bulky exploration). Give each sub-agent a self-contained brief; it cannot see this conversation.

# Skills
Playbooks you can load with `load_skill` before doing matching work:
{skills}

# Final answer
Call `finish` with: the direct result first (the answer, exact values, or what was built and where),
then a few short lines on how you verified it and any caveats. No filler."""

SUBAGENT_ADDENDUM = """

# Your role: sub-agent
You are a sub-agent handling one part of a larger task for a parent agent that cannot see your work.
Stay strictly within your assigned scope. Your `finish` answer is your report to the parent: make it
information-dense and self-contained (exact findings, values, file paths, sources, and anything you
could not determine). Keep it under ~400 words unless the brief asks for more."""

_PROBE_TOOLS = ["python3", "pip", "git", "node", "npm", "rg", "jq", "sqlite3", "curl", "wget", "make", "gcc", "go", "cargo", "java", "docker"]


def build_system_prompt(skills_index: str, *, subagent: bool = False, extra: str | None = None) -> str:
    text = SYSTEM_PROMPT.format(skills=skills_index or "(none installed)")
    if subagent:
        text += SUBAGENT_ADDENDUM
    if extra:
        text += "\n\n" + extra.strip()
    return text


def environment_snapshot(workspace: Path) -> str:
    tools = [t for t in _PROBE_TOOLS if shutil.which(t)]
    listing = _listing(workspace)
    return (
        f"- Date (UTC): {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}\n"
        f"- OS: {platform.system()} {platform.release()} ({platform.machine()}); Python {sys.version.split()[0]}\n"
        f"- Available CLIs: {', '.join(tools) or 'none detected'}\n"
        f"- Working directory (workspace): {workspace}\n"
        f"- Workspace contents:\n{listing}"
    )


def _listing(root: Path, max_entries: int = 60) -> str:
    if not root.exists():
        return "  (workspace does not exist yet)"
    lines: list[str] = []
    skip = {".git", "node_modules", "__pycache__", ".venv", "venv"}
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in skip)
        rel = Path(dirpath).relative_to(root)
        depth = len(rel.parts)
        if depth > 2:
            dirnames[:] = []
            continue
        for f in sorted(filenames):
            if count >= max_entries:
                lines.append("  … (more files; use glob/ls to explore)")
                return "\n".join(lines)
            try:
                size = (Path(dirpath) / f).stat().st_size
            except OSError:
                size = 0
            lines.append(f"  {(rel / f).as_posix()} ({size:,} B)")
            count += 1
    return "\n".join(lines) if lines else "  (empty)"


def build_task_message(
    task: TaskSpec,
    *,
    workspace: Path,
    memories: list[str] | None = None,
    hints: list[str] | None = None,
    playbook: tuple[str, str] | None = None,
) -> str:
    parts = [f"# Task\n{task.instruction.strip()}"]
    if task.context:
        parts.append(f"# Context from the requester\n{task.context.strip()}")
    if task.acceptance_criteria:
        parts.append("# Acceptance criteria (your result will be checked against these)\n" + "\n".join(f"- {c}" for c in task.acceptance_criteria))
    if task.verify_command:
        parts.append(f"# Verification\nWhen you call finish, the harness will run `{task.verify_command}` in the workspace; it must exit 0.")
    parts.append(f"# Environment\n{environment_snapshot(workspace)}")
    if memories:
        parts.append("# Possibly relevant memories from past sessions\n" + "\n".join(memories))
    if hints:
        parts.append("# Harness hints\n" + "\n".join(f"- {h}" for h in hints))
    if playbook:
        parts.append(f"# Playbook: {playbook[0]} (skill auto-loaded because it matches this task)\n{playbook[1]}")
    return "\n\n".join(parts)


def describe_budget(b: Any) -> str:
    return f"up to {b.max_turns} turns and {b.max_tool_calls} tool calls"
