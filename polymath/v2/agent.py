"""Polymath v2 agent: the prebuilt Pydantic AI ``Coder`` composition with measured replacements.

Starting point = a real ``pydantic_ai_harness.coder.Coder``: its instructions, ``FileSystem``,
``RepoContext``, ``RepairToolArguments`` and ``WarnNearLimits`` are used as shipped. Each Polymath
component REPLACES one prebuilt part and can be switched off, so every difference in an
experiment traces to exactly one swap:

| flag | prebuilt part (off) | Polymath part (on) | evidence |
|---|---|---|---|
| ``terminal`` | Coder ``shell`` (10/14) | ``PolymathTerminal`` (14/14) | research R3 |
| ``recall`` | ``ClearToolResults(max_fraction=0.7)`` + 64k truncation | ``RecallableEviction`` + ``ToolOutputLimits`` spill | R2 H1/H3/H5 |
| ``ledger`` | none | ``StateLedger`` | R2 H2 |

Model access goes through ``POLYMATH_BASE_URL`` (e.g. the egress governor, research R4).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic_ai import Agent
from pydantic_ai_harness.coder import Coder
from pydantic_ai_harness.compaction import ClearToolResults
from pydantic_ai_harness.shell import Shell
from pydantic_ai_harness.tool_output_limits import Band, LocalFileStore, Spill, ToolOutputLimits, Truncate

from ..config import DEFAULT_MODEL_PARAMS, NIM_BASE_URL
from .ledger import StateLedger
from .recall import RecallableEviction, RecallStats
from .terminal import PolymathTerminal

MAX_OUTPUT_CHARS = 64_000  # Coder's cap, kept as the spill fallback
SPILL_OVER_CHARS = 24_000  # above this a result is stored whole and the model gets a handle + preview


@dataclass
class V2Options:
    terminal: bool = True
    recall: bool = True
    ledger: bool = True
    addressable: bool = True  # False: same eviction policy, irreversible placeholder (the H1 control arm)
    stub_tail_lines: int = 3  # lines of each evicted result's tail shown in its stub (R6 §6)
    # None → per-model default for Polymath's tiers, and Coder's clearing keeps its own resolution.
    # Set explicitly (a stress regime), it applies to Coder's ClearToolResults too, so every arm
    # works under the same pressure.
    context_window: int | None = None
    store_dir: Path | None = None


@dataclass
class V2Telemetry:
    """What an experiment reads back after a run."""

    recall_runs: list[RecallStats] = field(default_factory=list)
    ledger_renders: list[str] = field(default_factory=list)


def build_model(model: str, *, base_url: str | None = None, api_key_env: str = "NVIDIA_NIM_API_KEY") -> Any:
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    url = base_url or os.environ.get("POLYMATH_BASE_URL", NIM_BASE_URL)
    return OpenAIChatModel(model, provider=OpenAIProvider(base_url=url, api_key=os.environ.get(api_key_env, "")))


def build_capabilities(workspace: str | Path, *, model_name: str = "", opts: V2Options | None = None) -> tuple[list[Any], V2Telemetry]:
    """Start from a real ``Coder`` and swap components out of it.

    Swapping instead of re-typing Coder's configuration guarantees that every part NOT swapped is
    byte-for-byte what Coder ships (timeouts, env filtering, output caps), so an ablation differs
    from the ``pydanticai-coder`` baseline only in the swapped parts, even across harness versions.
    """
    opts = opts or V2Options()
    root = Path(workspace).resolve()
    window = opts.context_window or DEFAULT_MODEL_PARAMS.get(model_name, {}).get("context_window", 128_000)
    tel = V2Telemetry()
    store = LocalFileStore(base_dir=opts.store_dir) if opts.store_dir else LocalFileStore()
    caps: list[Any] = []
    for part in Coder(workspace=root).capabilities:
        if isinstance(part, Shell) and opts.terminal:
            caps.append(PolymathTerminal(root))
        elif isinstance(part, ClearToolResults) and opts.recall:
            caps.append(RecallableEviction(context_window=window, store=store, history=tel.recall_runs, addressable=opts.addressable,
                                           stub_tail_lines=opts.stub_tail_lines))
        elif isinstance(part, ClearToolResults) and opts.context_window:
            caps.append(ClearToolResults(max_fraction=0.7, context_window=opts.context_window))  # Coder's setting, same window
        elif isinstance(part, ToolOutputLimits) and opts.recall:
            # Coder truncates at 64k with no read tool; v2 spills losslessly above 24k (handle + preview).
            caps.append(ToolOutputLimits(bands=[Band(over=SPILL_OVER_CHARS, action=Spill(then=Truncate(max_chars=MAX_OUTPUT_CHARS)))], store=store))
        else:
            caps.append(part)
    if opts.ledger:
        caps.append(StateLedger(workspace=root, renders=tel.ledger_renders))
    return caps, tel


def build_agent(model: str, workspace: str | Path, *, opts: V2Options | None = None, base_url: str | None = None, model_obj: Any = None) -> tuple[Agent, V2Telemetry]:
    caps, tel = build_capabilities(workspace, model_name=model, opts=opts)
    return Agent(model_obj if model_obj is not None else build_model(model, base_url=base_url), capabilities=caps), tel
