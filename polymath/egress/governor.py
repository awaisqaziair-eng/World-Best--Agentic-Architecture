"""Egress governor: one adaptive rate controller shared by every agent stack on an account.

Why it exists (measured; docs/research/egress-governor.md):

* Provider rate limits are per ACCOUNT, but every SDK retries per CLIENT. Clients that each back
  off independently still collide. In the v1.2 GLM core run, 9 of 28 tasks died of HTTP 429
  after 7 client-side retries each; the same storm hit the first three-stack bake-off.
* NVIDIA NIM also reports overload INSIDE a 200 response: the first SSE event is
  ``data: {"error": "Service temporarily overloaded"}``. HTTP-layer retries (openai ``max_retries``,
  Pydantic AI's tenacity transports) never see it, because the status line said 200.

What it is: an OpenAI-compatible reverse proxy. A client points its ``base_url`` here and nothing
else changes, so LangChain, deepagents, Pydantic AI and Polymath get identical infrastructure
behaviour. That is what makes cross-stack comparisons fair.

1. **Admission.** A FIFO token bucket at rate R (requests/s), plus a cap on requests in flight.
2. **Loss-tolerant AIMD.** Each success adds ``alpha`` to R. A throttle signal (429, 503,
   in-body overload) cuts R by ``beta`` ONLY when the throttle fraction over the last
   ``throttle_window`` attempts is at least ``decrease_above``, and at most once per
   ``cooldown_s``. Plain AIMD treats every 429 as congestion we caused. Measured on NIM, that
   is false: 429s kept arriving at 4 req/min, and were *more* frequent with nothing of ours in
   flight (66 %) than with 3–5 requests in flight (5 %). They were provider-side capacity
   throttling, and plain AIMD drove throughput to its floor for nothing. Congestion we cause
   shows up as a high refusal fraction; background throttling as a steady low one, which BBR-
   style congestion control likewise refuses to read as congestion. Below the threshold a
   throttle only delays its own request (backoff, honouring ``Retry-After``). Above it,
   ``Retry-After`` also pauses admission for everyone, because the limit is then ours.
3. **Retry before commit.** 429, 5xx, connection errors and an error as the first stream event
   are retried while nothing has been sent to the client. Once the first byte is forwarded the
   response is committed, and later errors pass through untouched. Nothing is ever duplicated.
4. **Observable.** ``GET /__governor/stats`` returns counters and the current rate; an optional
   JSONL log records every request: attempts, statuses, queue wait, outcome.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from email.utils import parsedate_to_datetime
from typing import Any, Callable

from aiohttp import ClientError, ClientSession, ClientTimeout, web

# Hop-by-hop and length/encoding headers are recomputed on each leg, never forwarded.
_HOP = {
    "host", "content-length", "transfer-encoding", "connection", "keep-alive", "proxy-authenticate",
    "proxy-authorization", "te", "trailers", "upgrade", "accept-encoding", "content-encoding",
}
_RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504}
_THROTTLE_STATUS = {429, 503}
_THROTTLE_TEXT = re.compile(r"overload|rate.?limit|too many|capacity|busy|429|try again", re.I)
_PEEK_LIMIT = 256 * 1024  # bytes read while looking for the first SSE data event


@dataclass
class GovernorConfig:
    upstream: str = "https://integrate.api.nvidia.com/v1"
    mount: str = "/v1"  # client path prefix that maps onto `upstream`
    rate: float = 0.5  # initial admission rate, requests/s
    min_rate: float = 0.1
    max_rate: float = 5.0
    alpha: float = 0.02  # additive increase per success, requests/s
    beta: float = 0.6  # multiplicative decrease per congestion episode
    cooldown_s: float = 10.0  # one decrease per window
    throttle_window: int = 40  # recent attempts used to judge whether throttling is load-induced
    decrease_above: float = 0.35  # throttle fraction at/above which throttling is treated as ours
    min_samples: int = 10  # don't judge on fewer attempts than this
    max_inflight: int = 8
    max_attempts: int = 10
    deadline_s: float = 900.0  # total time a request may spend retrying before the last error is returned
    backoff_base_s: float = 2.0
    backoff_cap_s: float = 60.0
    connect_timeout_s: float = 30.0
    idle_timeout_s: float = 300.0  # max silence between upstream bytes
    api_key_env: str = "NVIDIA_NIM_API_KEY"  # injected only when the client sent no Authorization
    log_path: str | None = None


@dataclass
class Stats:
    requests: int = 0
    completed: int = 0
    passed_through_errors: int = 0
    gave_up: int = 0
    attempts: int = 0
    retries: int = 0
    throttles: int = 0
    in_body_errors: int = 0
    connection_errors: int = 0
    rate_decreases: int = 0
    committed_stream_errors: int = 0
    queue_wait_s: float = 0.0
    max_queue_wait_s: float = 0.0
    status_counts: dict[str, int] = field(default_factory=dict)


class AimdLimiter:
    """FIFO token bucket whose rate follows additive-increase / multiplicative-decrease."""

    def __init__(self, cfg: GovernorConfig, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.cfg = cfg
        self.clock = clock
        self.rate = cfg.rate
        self._next_slot = 0.0
        self._paused_until = 0.0
        self._last_decrease = float("-inf")
        self._lock = asyncio.Lock()
        self.inflight = asyncio.Semaphore(cfg.max_inflight)
        self.decreases = 0
        self._outcomes: deque[bool] = deque(maxlen=cfg.throttle_window)  # True = throttled

    @property
    def throttle_fraction(self) -> float:
        return sum(self._outcomes) / len(self._outcomes) if self._outcomes else 0.0

    @property
    def load_induced(self) -> bool:
        return len(self._outcomes) >= self.cfg.min_samples and self.throttle_fraction >= self.cfg.decrease_above

    def reserve(self) -> float:
        """Claim the next admission slot; returns the absolute time it opens (FIFO by call order)."""
        now = self.clock()
        slot = max(now, self._next_slot, self._paused_until)
        self._next_slot = slot + 1.0 / self.rate
        return slot

    async def admit(self) -> float:
        """Wait for an admission slot; returns seconds spent waiting."""
        start = self.clock()
        while True:
            async with self._lock:
                slot = self.reserve()
            delay = slot - self.clock()
            if delay > 0:
                await asyncio.sleep(delay)
            if self.clock() >= self._paused_until:  # a Retry-After pause set while we slept re-queues us
                return self.clock() - start

    def on_success(self) -> None:
        self._outcomes.append(False)
        self.rate = min(self.cfg.max_rate, self.rate + self.cfg.alpha)

    def on_throttle(self, retry_after: float | None = None) -> None:
        """Record a throttle; cut the shared rate only if throttling looks load-induced."""
        self._outcomes.append(True)
        if not self.load_induced:
            return  # background throttling: the request's own backoff handles it
        now = self.clock()
        if retry_after:
            self._paused_until = max(self._paused_until, now + retry_after)
        if now - self._last_decrease >= self.cfg.cooldown_s:
            self.rate = max(self.cfg.min_rate, self.rate * self.cfg.beta)
            self._last_decrease = now
            self.decreases += 1
            # Re-space the queue at the new rate from now on.
            self._next_slot = max(self._next_slot, now + 1.0 / self.rate)


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


def in_body_error(payload: bytes) -> str | None:
    """Return the provider error message if `payload` is an OpenAI-style error object, else None."""
    try:
        obj = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return None
    if isinstance(obj, dict) and obj.get("error") and not obj.get("choices"):
        err = obj["error"]
        return str(err.get("message", err) if isinstance(err, dict) else err)
    return None


class Governor:
    def __init__(self, cfg: GovernorConfig) -> None:
        self.cfg = cfg
        self.limiter = AimdLimiter(cfg)
        self.stats = Stats()
        self._session: ClientSession | None = None
        self._log = open(cfg.log_path, "a", encoding="utf-8") if cfg.log_path else None  # noqa: SIM115

    # ── lifecycle ───────────────────────────────────────────────────────
    def app(self) -> web.Application:
        app = web.Application(client_max_size=64 * 1024 * 1024)
        app.router.add_get("/__governor/stats", self._stats)
        app.router.add_route("*", "/{tail:.*}", self._proxy)
        app.on_startup.append(self._start)
        app.on_cleanup.append(self._stop)
        return app

    async def _start(self, _app: web.Application) -> None:
        self._session = ClientSession(auto_decompress=False)

    async def _stop(self, _app: web.Application) -> None:
        if self._session:
            await self._session.close()
        if self._log:
            self._log.close()

    async def _stats(self, _request: web.Request) -> web.Response:
        body = asdict(self.stats) | {"rate_rps": round(self.limiter.rate, 4), "inflight_cap": self.cfg.max_inflight,
                                     "throttle_fraction": round(self.limiter.throttle_fraction, 3), "load_induced": self.limiter.load_induced}
        return web.json_response(body)

    # ── proxy ───────────────────────────────────────────────────────────
    def _upstream_url(self, request: web.Request) -> str:
        path = request.rel_url.path
        if path.startswith(self.cfg.mount):
            path = path[len(self.cfg.mount):]
        qs = f"?{request.rel_url.query_string}" if request.rel_url.query_string else ""
        return self.cfg.upstream.rstrip("/") + path + qs

    def _headers(self, request: web.Request) -> dict[str, str]:
        headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP}
        if "authorization" not in {k.lower() for k in headers} and os.environ.get(self.cfg.api_key_env):
            headers["Authorization"] = f"Bearer {os.environ[self.cfg.api_key_env]}"
        headers["Accept-Encoding"] = "identity"  # we inspect bodies; never let the upstream compress them
        return headers

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return min(self.cfg.backoff_cap_s, retry_after)
        return random.uniform(0.5, 1.0) * min(self.cfg.backoff_cap_s, self.cfg.backoff_base_s * 2 ** (attempt - 1))

    def _count(self, status: int | str) -> None:
        key = str(status)
        self.stats.status_counts[key] = self.stats.status_counts.get(key, 0) + 1

    async def _proxy(self, request: web.Request) -> web.StreamResponse:
        st = self.stats
        st.requests += 1
        body = await request.read()
        url, headers, t0 = self._upstream_url(request), self._headers(request), time.monotonic()
        rec: dict[str, Any] = {"ts": time.time(), "path": request.rel_url.path, "model": _model_of(body), "statuses": []}
        last: tuple[int, bytes, dict[str, str]] = (502, b'{"error":{"message":"governor: upstream unreachable"}}', {})
        attempt = 0
        while True:
            attempt += 1
            waited = await self.limiter.admit()
            st.queue_wait_s += waited
            st.max_queue_wait_s = max(st.max_queue_wait_s, waited)
            rec["queue_wait_s"] = round(rec.get("queue_wait_s", 0.0) + waited, 3)
            st.attempts += 1
            retry_after: float | None = None
            async with self.limiter.inflight:
                outcome: Any = None
                try:
                    assert self._session is not None
                    resp = await self._session.request(
                        request.method, url, data=body, headers=headers,
                        timeout=ClientTimeout(total=None, sock_connect=self.cfg.connect_timeout_s, sock_read=self.cfg.idle_timeout_s),
                    )
                    try:
                        # Anything raised here escaped _attempt BEFORE commit (e.g. an idle timeout while
                        # peeking the first stream event): nothing reached the client, so it is retryable.
                        outcome = await self._attempt(request, resp, rec)
                    finally:
                        resp.release()
                except (ClientError, asyncio.TimeoutError) as e:
                    st.connection_errors += 1
                    self._count("conn")
                    rec["statuses"].append("conn")
                    last = (502, json.dumps({"error": {"message": f"governor: {type(e).__name__}: {e}"}}).encode(), {})
                if outcome is not None:
                    if isinstance(outcome, web.StreamResponse):
                        self.limiter.on_success()
                        st.completed += 1
                        return self._finish(rec, t0, attempt, "ok", outcome)
                    status, payload, keep_headers, retryable, throttled = outcome
                    last = (status, payload, keep_headers)
                    if not retryable:
                        st.passed_through_errors += 1
                        return self._finish(rec, t0, attempt, "passed_through", _response(status, payload, keep_headers))
                    if throttled:
                        st.throttles += 1
                        retry_after = parse_retry_after(resp.headers.get("Retry-After"))
                        before = self.limiter.decreases
                        self.limiter.on_throttle(retry_after)
                        st.rate_decreases += self.limiter.decreases - before
            delay = self._backoff(attempt, retry_after)
            if attempt >= self.cfg.max_attempts or time.monotonic() - t0 + delay > self.cfg.deadline_s:
                st.gave_up += 1
                status, payload, keep_headers = last
                return self._finish(rec, t0, attempt, "gave_up", _response(status, payload, keep_headers))
            st.retries += 1
            await asyncio.sleep(delay)

    async def _attempt(self, request: web.Request, resp: Any, rec: dict[str, Any]) -> Any:
        """Forward a good response (returns the StreamResponse) or classify a failure.

        Failure tuple: (status, payload, headers_to_keep, retryable, throttle_signal).
        """
        st = self.stats
        status = resp.status
        self._count(status)
        rec["statuses"].append(status)
        ctype = resp.headers.get("Content-Type", "")
        keep = {"Content-Type": ctype} if ctype else {}
        if status >= 400:
            payload = await resp.read()
            retryable = status in _RETRY_STATUS
            return status, payload, keep, retryable, status in _THROTTLE_STATUS
        if "text/event-stream" in ctype:
            head, err = await _peek_first_event(resp)
            if err is not None:
                st.in_body_errors += 1
                rec["statuses"].append("in_body_error")
                return 503, json.dumps({"error": {"message": err}}).encode(), {"Content-Type": "application/json"}, True, bool(_THROTTLE_TEXT.search(err))
            out = web.StreamResponse(status=status, headers={"Content-Type": ctype, "Cache-Control": "no-cache"})
            await out.prepare(request)
            await out.write(head)  # committed: from here on errors are the client's to see
            try:
                async for chunk in resp.content.iter_any():
                    await out.write(chunk)
            except (ClientError, asyncio.TimeoutError, ConnectionResetError):
                st.committed_stream_errors += 1
                rec["statuses"].append("committed_stream_error")
            try:
                await out.write_eof()
            except ConnectionResetError:  # the client went away; nothing left to tell it
                pass
            return out
        payload = await resp.read()
        err = in_body_error(payload)
        if err is not None:
            st.in_body_errors += 1
            rec["statuses"].append("in_body_error")
            return 503, payload, keep, True, bool(_THROTTLE_TEXT.search(err))
        out = web.Response(status=status, body=payload, headers=keep)
        return out

    def _finish(self, rec: dict[str, Any], t0: float, attempts: int, outcome: str, resp: web.StreamResponse) -> web.StreamResponse:
        rec.update(attempts=attempts, outcome=outcome, duration_s=round(time.monotonic() - t0, 3), rate_rps=round(self.limiter.rate, 4),
                   throttle_fraction=round(self.limiter.throttle_fraction, 3))
        if self._log:
            self._log.write(json.dumps(rec) + "\n")
            self._log.flush()
        return resp


async def _peek_first_event(resp: Any) -> tuple[bytes, str | None]:
    """Buffer the stream up to its first `data:` event; report an error if that event is one."""
    head = b""
    while len(head) < _PEEK_LIMIT:
        line = await resp.content.readline()
        if not line:
            break
        head += line
        if line.startswith(b"data:"):
            payload = line[5:].strip()
            if payload == b"[DONE]":
                break
            return head, in_body_error(payload)
    return head, None


def _response(status: int, payload: bytes, headers: dict[str, str]) -> web.Response:
    return web.Response(status=status, body=payload, headers=headers or {"Content-Type": "application/json"})


def _model_of(body: bytes) -> str | None:
    try:
        obj = json.loads(body)
        return obj.get("model") if isinstance(obj, dict) else None
    except (ValueError, UnicodeDecodeError):
        return None
