"""Skills: procedural memory with progressive disclosure.

A skill is a directory containing ``SKILL.md`` with YAML-like front matter::

    ---
    name: data-analysis
    description: One line saying when to use this skill.
    ---
    <instructions, checklists, snippets>

Only ``name`` + ``description`` of every skill are placed in the system prompt
(a few tokens each). The body is loaded on demand with ``load_skill`` — so an
agent can have hundreds of skills without paying for them in context.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import Tool, ToolContext, ToolError, ToolOutput

BUILTIN_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
_FM = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)


@dataclass
class Skill:
    name: str
    description: str
    path: Path
    body: str


def parse_skill(md_path: Path) -> Skill | None:
    text = md_path.read_text(encoding="utf-8")
    m = _FM.match(text)
    if not m:
        return None
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip("'\"")
    if "name" not in meta:
        return None
    return Skill(meta["name"], meta.get("description", ""), md_path.parent, m.group(2).strip())


def discover_skills(dirs: list[str | Path]) -> dict[str, Skill]:
    skills: dict[str, Skill] = {}
    for d in [BUILTIN_SKILLS_DIR, *map(Path, dirs)]:
        d = Path(d).expanduser()
        if not d.is_dir():
            continue
        for md in sorted(d.glob("*/SKILL.md")):
            s = parse_skill(md)
            if s:
                skills[s.name] = s  # later dirs override built-ins
    return skills


def skills_index(skills: dict[str, Skill]) -> str:
    if not skills:
        return ""
    lines = [f"- {s.name}: {s.description}" for s in sorted(skills.values(), key=lambda s: s.name)]
    return "\n".join(lines)


class LoadSkillTool(Tool):
    name = "load_skill"
    parallel_safe = True
    description = """\
Load a playbook listed under Skills in the system prompt, before doing that kind of work."""
    parameters = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }

    def __init__(self, skills: dict[str, Skill]) -> None:
        self.skills = skills

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        s = self.skills.get(args["name"])
        if s is None:
            raise ToolError(f"unknown skill {args['name']!r}; available: {sorted(self.skills)}")
        extra = sorted(p.relative_to(s.path).as_posix() for p in s.path.rglob("*") if p.is_file() and p.name != "SKILL.md")
        files = f"\n\nSkill resources in {s.path}: {extra}" if extra else ""
        ctx.state.setdefault("skills_loaded", []).append(s.name)
        return ToolOutput(f"# Skill: {s.name}\n\n{s.body}{files}")
