"""OpenAI-compatible chat-completions client (NVIDIA NIM, vLLM, OpenAI, …).

Resilience built in:
* exponential backoff with full jitter, honouring ``Retry-After``;
* automatic downgrade from native to text tool protocol when an endpoint
  rejects the ``tools`` parameter (``tool_protocol="auto"``);
* context-overflow detection surfaced as a typed error so the kernel can
  compact and retry instead of failing;
* malformed/empty 200 responses treated as transient.

The HTTP transport is injectable, which is how the chaos tests inject
timeouts, 429 storms and garbage payloads deterministically.
"""

from __future__ import annotations

import json
import random
import socket
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from .. import jsonutil
from ..types import ModelResponse, Usage
from .base import (
    AUTH,
    BAD_REQUEST,
    CONTEXT_OVERFLOW,
    NOT_FOUND,
    RETRY_EXHAUSTED,
    ChatRequest,
    ModelError,
)
from .protocols import NativeProtocol, TextProtocol

Transport = Callable[[str, dict[str, str], bytes, float], tuple[int, dict[str, str], bytes]]

RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524})
_OVERFLOW_HINTS = ("context length", "context_length", "maximum context", "too many tokens", "prompt is too long", "input is too long", "exceeds the model")
_TOOLS_UNSUPPORTED_HINTS = ("tool", "function call", "tool_choice")


class TransportError(Exception):
    pass


def urllib_transport(url: str, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as e:
        try:
            payload = e.read()
        except Exception:
            payload = b""
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, payload
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
        raise TransportError(f"{type(e).__name__}: {e}") from e


class OpenAICompatClient:
    def __init__(
        self,
        model: str,
        *,
        base_url: str,
        api_key: str | None,
        protocol: str = "auto",
        timeout_s: float = 240.0,
        max_retries: int = 6,
        retry_base_s: float = 1.5,
        retry_cap_s: float = 45.0,
        default_temperature: float = 0.3,
        default_max_tokens: int = 16_384,
        extra_body: dict[str, Any] | None = None,
        send_reasoning_back: bool = False,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.protocol_mode = protocol
        self._text = protocol == "text"
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.retry_base_s = retry_base_s
        self.retry_cap_s = retry_cap_s
        self.default_temperature = default_temperature
        self.default_max_tokens = default_max_tokens
        self.extra_body = dict(extra_body or {})
        self.native = NativeProtocol(send_reasoning_back=send_reasoning_back)
        self.textp = TextProtocol()
        self.transport = transport or urllib_transport
        self.sleep = sleep
        self.rng = rng or random.Random()
        self._counter = 0

    @property
    def protocol(self) -> str:
        return "text" if self._text else "native"

    # ── backoff ─────────────────────────────────────────────────────────
    def backoff_delay(self, attempt: int, retry_after: str | None = None) -> float:
        if retry_after:
            try:
                return min(self.retry_cap_s, max(0.0, float(retry_after)))
            except ValueError:
                pass
        ceiling = min(self.retry_cap_s, self.retry_base_s * (2 ** (attempt - 1)))
        return self.rng.uniform(0.25 * ceiling, ceiling)  # "full jitter" with a floor

    # ── request building ────────────────────────────────────────────────
    def build_body(self, req: ChatRequest) -> dict[str, Any]:
        proto = self.textp if self._text else self.native
        wire, extra = proto.encode(req.messages, req.tools)
        body: dict[str, Any] = {
            "model": self.model,
            "messages": wire,
            "temperature": self.default_temperature if req.temperature is None else req.temperature,
            "max_tokens": req.max_tokens or self.default_max_tokens,
            "stream": False,
        }
        if not self._text and req.tools:
            body.update(extra)
            if req.tool_choice is not None:
                body["tool_choice"] = req.tool_choice
        if req.seed is not None:
            body["seed"] = req.seed
        body.update(self.extra_body)
        return body

    # ── main entry ──────────────────────────────────────────────────────
    def complete(self, request: ChatRequest) -> ModelResponse:
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        attempt = 0
        downgraded = False
        started = time.monotonic()
        last_err = "unknown"
        while True:
            body = self.build_body(request)
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            retry_after = None
            try:
                status, rh, raw = self.transport(url, headers, payload, self.timeout_s)
            except TransportError as e:
                status, rh, raw, last_err = -1, {}, b"", str(e)
            if status == 200:
                try:
                    data = json.loads(raw)
                    choice = (data.get("choices") or [None])[0]
                    if not choice or "message" not in choice:
                        raise ValueError("response has no choices[0].message")
                    return self._to_response(data, choice, started, attempt + 1)
                except (ValueError, json.JSONDecodeError, TypeError, AttributeError) as e:
                    last_err = f"malformed 200 response: {e}; body={raw[:300]!r}"
            elif status in RETRYABLE_STATUS:
                retry_after = rh.get("retry-after")
                last_err = f"HTTP {status}: {raw[:300].decode('utf-8', 'replace')}"
            elif status != -1:
                text = raw.decode("utf-8", "replace")
                low = text.lower()
                if status in (400, 413, 422) and any(h in low for h in _OVERFLOW_HINTS):
                    raise ModelError(f"context overflow: {text[:400]}", kind=CONTEXT_OVERFLOW, model=self.model, status=status)
                if (
                    status in (400, 422)
                    and self.protocol_mode == "auto"
                    and not self._text
                    and request.tools
                    and not downgraded
                    and any(h in low for h in _TOOLS_UNSUPPORTED_HINTS)
                ):
                    self._text = True  # sticky: this endpoint does not do native tools
                    downgraded = True
                    continue
                kind = {401: AUTH, 403: AUTH, 404: NOT_FOUND}.get(status, BAD_REQUEST)
                raise ModelError(f"HTTP {status}: {text[:600]}", kind=kind, model=self.model, status=status)
            attempt += 1
            if attempt > self.max_retries:
                raise ModelError(
                    f"gave up after {attempt} attempts: {last_err}", kind=RETRY_EXHAUSTED, model=self.model, status=status if status > 0 else None
                )
            self.sleep(self.backoff_delay(attempt, retry_after))

    def _to_response(self, data: dict[str, Any], choice: dict[str, Any], started: float, attempts: int) -> ModelResponse:
        self._counter += 1
        proto = self.textp if self._text else self.native
        salt = jsonutil.digest([self.model, self._counter, data.get("id")], 8)
        content, calls, reasoning = proto.decode(choice["message"], salt=salt)
        u = data.get("usage") or {}
        details = u.get("prompt_tokens_details") or {}
        usage = Usage(
            input_tokens=int(u.get("prompt_tokens") or 0),
            output_tokens=int(u.get("completion_tokens") or 0),
            cached_tokens=int(details.get("cached_tokens") or 0),
            requests=1,
        )
        return ModelResponse(
            content=content,
            tool_calls=calls,
            usage=usage,
            model=data.get("model") or self.model,
            finish_reason=choice.get("finish_reason"),
            reasoning=reasoning,
            latency_s=time.monotonic() - started,
            protocol=self.protocol,
            attempts=attempts,
        )
