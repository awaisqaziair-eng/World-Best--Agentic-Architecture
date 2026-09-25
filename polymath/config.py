"""Layered configuration.

Precedence (highest wins): explicit overrides (CLI / API) > environment
variables > ``polymath.toml`` (cwd, then ``~/.polymath/``) > built-in defaults.

Every field is documented in docs/16-configuration-and-operations.md.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"

# Per-model knobs discovered empirically on NVIDIA NIM (see docs/evaluation).
DEFAULT_MODEL_PARAMS: dict[str, dict[str, Any]] = {
    # GLM-5.3 reasons at length (measured: a 6k-token budget spent entirely on reasoning) → larger output budget.
    "z-ai/glm-5.3": {"temperature": 0.3, "context_window": 200_000, "max_output_tokens": 32_768},
    "z-ai/glm-5.3-flash": {"temperature": 0.3, "context_window": 200_000},
    "moonshotai/kimi-k3": {"temperature": 0.6, "context_window": 256_000},
    "nvidia/nemotron-3-ultra-550b-a55b": {"temperature": 0.3, "context_window": 256_000},
    "nvidia/nemotron-3-super-120b-a12b": {"temperature": 0.3, "context_window": 256_000},
    "nvidia/nemotron-3.5-lightning-30b-a3b": {"temperature": 0.2, "context_window": 128_000},
    "openai/gpt-oss-20b": {"temperature": 0.5, "context_window": 128_000},
    "google/gemma-4-31b-it": {"temperature": 0.3, "context_window": 128_000},
}


@dataclass
class Config:
    # ── Model gateway ───────────────────────────────────────────────────
    base_url: str = NIM_BASE_URL
    api_key_env: str = "NVIDIA_NIM_API_KEY"
    model: str = "z-ai/glm-5.3"
    fallback_models: list[str] = field(
        default_factory=lambda: ["nvidia/nemotron-3-super-120b-a12b", "nvidia/nemotron-3-ultra-550b-a55b"]
    )
    utility_model: str = "nvidia/nemotron-3.5-lightning-30b-a3b"  # compaction, routing, judging
    tool_protocol: str = "auto"  # auto | native | text
    temperature: float | None = None  # None → per-model default
    max_output_tokens: int = 16_384
    stream: bool = True  # SSE streaming: request_timeout_s becomes an idle (per-chunk) timeout
    request_timeout_s: float = 240.0
    max_request_s: float = 900.0  # total cap per streamed request
    max_retries: int = 6
    retry_base_s: float = 1.5
    retry_cap_s: float = 45.0
    breaker_failures: int = 3
    breaker_cooldown_s: float = 90.0
    seed: int | None = None
    send_reasoning_back: bool = False
    model_params: dict[str, dict[str, Any]] = field(default_factory=lambda: dict(DEFAULT_MODEL_PARAMS))

    # ── Context engine ──────────────────────────────────────────────────
    context_window: int | None = None  # None → per-model default
    clear_at: float = 0.45          # fraction of window that triggers tool-result clearing
    compact_at: float = 0.70        # fraction of window that triggers compaction
    keep_recent_tool_results: int = 8
    keep_recent_turns: int = 6
    clear_min_chars: int = 1_200

    # ── Tools ───────────────────────────────────────────────────────────
    shell: str = "/bin/bash"
    shell_timeout_s: float = 180.0
    shell_max_timeout_s: float = 1_800.0
    max_tool_output_chars: int = 24_000
    tool_parallelism: int = 8
    enable_web: bool = True
    enable_memory: bool = True
    enable_delegation: bool = True
    max_delegation_depth: int = 1
    max_parallel_subagents: int = 4
    hermetic_env_strip: list[str] = field(default_factory=lambda: ["*_API_KEY", "*_TOKEN", "*_SECRET"])
    skill_dirs: list[str] = field(default_factory=list)

    # ── Kernel ──────────────────────────────────────────────────────────
    max_turns: int = 80
    max_tokens: int = 3_000_000
    max_wall_s: float = 3_600.0
    max_tool_calls: int = 400
    verify: str = "auto"  # auto | off | always
    max_verify_rounds: int = 2
    router: str = "heuristic"  # heuristic | llm | off
    loop_window: int = 10
    loop_repeat_threshold: int = 3
    text_answer_grace: int = 2  # prose-only turns before accepting prose as final

    # ── Storage / observability ─────────────────────────────────────────
    home: str = str(Path.home() / ".polymath")
    sessions_dir: str | None = None
    memory_path: str | None = None
    fsync_events: bool = False
    verbosity: int = 1  # 0 quiet, 1 steps, 2 steps+reasoning

    # ── helpers ─────────────────────────────────────────────────────────
    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env)

    def sessions_path(self) -> Path:
        return Path(self.sessions_dir or Path(self.home) / "sessions")

    def memory_file(self) -> Path:
        return Path(self.memory_path or Path(self.home) / "memory.jsonl")

    def params_for(self, model: str) -> dict[str, Any]:
        return dict(self.model_params.get(model, {}))

    def temperature_for(self, model: str) -> float:
        if self.temperature is not None:
            return self.temperature
        return float(self.params_for(model).get("temperature", 0.3))

    def max_output_for(self, model: str) -> int:
        return int(self.params_for(model).get("max_output_tokens", self.max_output_tokens))

    def window_for(self, model: str) -> int:
        if self.context_window:
            return self.context_window
        return int(self.params_for(model).get("context_window", 128_000))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def replace(self, **overrides: Any) -> "Config":
        d = asdict(self)
        for k, v in overrides.items():
            if v is None:
                continue
            if k not in d:
                raise KeyError(f"unknown config key: {k}")
            d[k] = v
        return Config(**d)


_ENV_MAP = {
    "POLYMATH_MODEL": ("model", str),
    "POLYMATH_BASE_URL": ("base_url", str),
    "POLYMATH_UTILITY_MODEL": ("utility_model", str),
    "POLYMATH_FALLBACK_MODELS": ("fallback_models", lambda s: [x.strip() for x in s.split(",") if x.strip()]),
    "POLYMATH_TOOL_PROTOCOL": ("tool_protocol", str),
    "POLYMATH_HOME": ("home", str),
    "POLYMATH_SESSIONS_DIR": ("sessions_dir", str),
    "POLYMATH_VERBOSITY": ("verbosity", int),
    "POLYMATH_MAX_TURNS": ("max_turns", int),
    "POLYMATH_CONTEXT_WINDOW": ("context_window", int),
}


def load_config(path: str | os.PathLike[str] | None = None, **overrides: Any) -> Config:
    cfg = Config()
    data: dict[str, Any] = {}
    candidates = [Path(path)] if path else [Path.cwd() / "polymath.toml", Path(cfg.home) / "polymath.toml"]
    for p in candidates:
        if p.is_file():
            with open(p, "rb") as fh:
                data = tomllib.load(fh)
            break
    known = {f.name for f in fields(Config)}
    flat: dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(v, dict) and k not in known:  # allow [sections] for readability
            flat.update(v)
        else:
            flat[k] = v
    if "model_params" in flat:
        merged = dict(DEFAULT_MODEL_PARAMS)
        merged.update(flat["model_params"])
        flat["model_params"] = merged
    unknown = set(flat) - known
    if unknown:
        raise KeyError(f"unknown config keys in {candidates[0]}: {sorted(unknown)}")
    cfg = cfg.replace(**flat)
    env_over = {}
    for env, (key, conv) in _ENV_MAP.items():
        if env in os.environ and os.environ[env] != "":
            env_over[key] = conv(os.environ[env])
    cfg = cfg.replace(**env_over)
    return cfg.replace(**overrides)
