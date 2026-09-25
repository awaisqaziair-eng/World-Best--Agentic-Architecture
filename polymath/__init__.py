"""Polymath — a generalist agent harness.

Brain (model loop) and hands (tools) are decoupled around an append-only,
event-sourced session log. See docs/02-system-architecture.md.
"""

__version__ = "1.2.0"

from .config import Config, load_config  # noqa: E402
from .types import Budget, RunResult, TaskSpec  # noqa: E402


def __getattr__(name: str):  # lazy to keep `import polymath` cheap
    if name == "Runtime":
        from .kernel.runtime import Runtime

        return Runtime
    if name == "Agent":
        from .kernel.agent import Agent

        return Agent
    raise AttributeError(name)


__all__ = ["Config", "load_config", "Budget", "RunResult", "TaskSpec", "Runtime", "Agent", "__version__"]
