"""Fail-over across models with per-model circuit breakers.

A breaker opens after ``failure_threshold`` consecutive failover-class errors
and stays open for ``cooldown_s``; afterwards one trial request is allowed
(half-open). If every breaker is open the chain still tries the breaker that
will close soonest rather than failing without trying — an agent run is worth
one more attempt.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from ..types import ModelResponse
from .base import ALL_FAILED, CONTEXT_OVERFLOW, FAILOVER_KINDS, ChatRequest, ModelClient, ModelError


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, cooldown_s: float = 90.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_s = cooldown_s
        self.clock = clock
        self.failures = 0
        self.opened_at: float | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            if self.opened_at is None:
                return "closed"
            if self.clock() - self.opened_at >= self.cooldown_s:
                return "half_open"
            return "open"

    def allow(self) -> bool:
        return self.state != "open"

    def record_success(self) -> None:
        with self._lock:
            self.failures = 0
            self.opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self.failures += 1
            if self.failures >= self.failure_threshold:
                self.opened_at = self.clock()

    def reopens_in(self) -> float:
        with self._lock:
            if self.opened_at is None:
                return 0.0
            return max(0.0, self.cooldown_s - (self.clock() - self.opened_at))


class FallbackClient:
    """Tries clients in priority order; the first healthy success wins."""

    def __init__(
        self,
        clients: list[ModelClient],
        *,
        failure_threshold: int = 3,
        cooldown_s: float = 90.0,
        clock: Callable[[], float] = time.monotonic,
        on_failover: Callable[[str, ModelError], None] | None = None,
    ) -> None:
        if not clients:
            raise ValueError("FallbackClient needs at least one client")
        self.clients = clients
        self.breakers = {id(c): CircuitBreaker(failure_threshold, cooldown_s, clock) for c in clients}
        self.on_failover = on_failover

    @property
    def model(self) -> str:
        return self.clients[0].model

    def complete(self, request: ChatRequest) -> ModelResponse:
        errors: list[ModelError] = []
        candidates = [c for c in self.clients if self.breakers[id(c)].allow()]
        if not candidates:
            candidates = [min(self.clients, key=lambda c: self.breakers[id(c)].reopens_in())]
        for client in candidates:
            br = self.breakers[id(client)]
            try:
                resp = client.complete(request)
            except ModelError as e:
                if e.kind == CONTEXT_OVERFLOW:
                    raise  # the kernel must shrink the context; another model will not help reliably
                if e.kind not in FAILOVER_KINDS:
                    raise  # a malformed request fails identically everywhere
                br.record_failure()
                errors.append(e)
                if self.on_failover:
                    self.on_failover(client.model, e)
                continue
            br.record_success()
            return resp
        raise ModelError(
            "all models failed: " + "; ".join(f"{e.model}: {e}" for e in errors)[:1200],
            kind=ALL_FAILED,
            model=self.model,
            causes=errors,
        )
