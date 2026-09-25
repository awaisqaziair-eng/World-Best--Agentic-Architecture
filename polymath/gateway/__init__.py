"""Model gateway: provider-neutral access to chat models."""

from __future__ import annotations

from typing import Any, Callable

from ..config import Config
from .base import ChatRequest, ModelClient, ModelError
from .openai_compat import OpenAICompatClient
from .resilience import FallbackClient


def build_client(
    cfg: Config,
    model: str | None = None,
    *,
    fallbacks: list[str] | None = None,
    on_failover: Callable[[str, ModelError], None] | None = None,
    **client_kw: Any,
) -> ModelClient:
    """Build a (possibly fail-over) client for ``model`` from configuration."""
    primary = model or cfg.model
    chain = [primary] + [m for m in (cfg.fallback_models if fallbacks is None else fallbacks) if m != primary]

    def one(m: str) -> OpenAICompatClient:
        params = cfg.params_for(m)
        return OpenAICompatClient(
            m,
            base_url=cfg.base_url,
            api_key=cfg.api_key,
            protocol=params.get("tool_protocol", cfg.tool_protocol),
            timeout_s=cfg.request_timeout_s,
            max_retries=cfg.max_retries,
            retry_base_s=cfg.retry_base_s,
            retry_cap_s=cfg.retry_cap_s,
            default_temperature=cfg.temperature_for(m),
            default_max_tokens=cfg.max_output_for(m),
            extra_body=params.get("extra_body"),
            send_reasoning_back=cfg.send_reasoning_back,
            stream=params.get("stream", cfg.stream),
            max_request_s=cfg.max_request_s,
            **client_kw,
        )

    clients = [one(m) for m in chain]
    if len(clients) == 1:
        return clients[0]
    return FallbackClient(clients, failure_threshold=cfg.breaker_failures, cooldown_s=cfg.breaker_cooldown_s, on_failover=on_failover)


__all__ = ["ChatRequest", "ModelClient", "ModelError", "OpenAICompatClient", "FallbackClient", "build_client"]
