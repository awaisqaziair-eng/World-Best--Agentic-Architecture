"""Tool runtime ("the hands")."""

from __future__ import annotations

from ..config import Config
from ..memory import MemoryStore
from .base import Tool, ToolContext, ToolError, ToolOutput, ToolRegistry
from .delegate import DelegateTool
from .files import EditFileTool, ReadFileTool, WriteFileTool
from .memory_tool import MemoryTool
from .planning import AskUserTool, FinishTool, NotesTool, TodoTool
from .search import GlobTool, GrepTool
from .skills import LoadSkillTool, Skill, discover_skills
from .terminal import BashTool
from .web import FetchUrlTool


def build_registry(
    cfg: Config,
    *,
    depth: int = 0,
    skills: dict[str, Skill] | None = None,
    memory: MemoryStore | None = None,
    minimal: bool = False,
) -> ToolRegistry:
    """Assemble the standard toolset.

    ``minimal=True`` yields the ablation baseline (bash + files + finish only),
    used by the evaluation suite to measure what the rest of the harness adds.
    """
    tools: list[Tool] = [BashTool(), ReadFileTool(), WriteFileTool(), EditFileTool(), FinishTool()]
    if minimal:
        return ToolRegistry(tools)
    tools += [GlobTool(), GrepTool(), TodoTool(), NotesTool(), AskUserTool()]
    if cfg.enable_web:
        tools.append(FetchUrlTool())
    if cfg.enable_memory and memory is not None:
        tools.append(MemoryTool(memory))
    if skills:
        tools.append(LoadSkillTool(skills))
    if cfg.enable_delegation and depth < cfg.max_delegation_depth:
        tools.append(DelegateTool())
    return ToolRegistry(tools)


__all__ = ["Tool", "ToolContext", "ToolError", "ToolOutput", "ToolRegistry", "build_registry", "discover_skills", "Skill"]
