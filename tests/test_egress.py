"""Egress governor: retry-before-commit semantics and AIMD admission, against a scripted fake upstream."""

from __future__ import annotations

import asyncio
import json
import os
import unittest

try:
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from polymath.egress import AimdLimiter, Governor, GovernorConfig, in_body_error, parse_retry_after
except ImportError:  # the stdlib-only v1 core runs without aiohttp
    web = None  # type: ignore[assignment]

GOOD = {"id": "x", "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}}]}


def sse(*events: dict | str) -> bytes:
    return b"".join(f"data: {e if isinstance(e, str) else json.dumps(e)}\n\n".encode() for e in events)


CHUNK = {"id": "x", "choices": [{"index": 0, "delta": {"content": "hel"}}]}
CHUNK2 = {"id": "x", "choices": [{"index": 0, "delta": {"content": "lo"}}]}
OVERLOAD = {"error": "Service temporarily overloaded"}


@unittest.skipIf(web is None, "aiohttp not installed")
class GovernorProxyTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.script: list[tuple] = []
        self.seen: list[dict] = []
        upstream = web.Application()

        async def handler(request: web.Request) -> web.StreamResponse:
            self.seen.append({"path": request.path, "auth": request.headers.get("Authorization"), "body": await request.text()})
            kind, *args = self.script.pop(0) if self.script else ("json", 200, GOOD)
            if kind == "json":
                status, obj = args[0], args[1]
                return web.json_response(obj, status=status, headers=args[2] if len(args) > 2 else None)
            resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await resp.prepare(request)
            if kind == "sse_stall":  # headers sent, then silence before the first event
                await asyncio.sleep(args[1])
            await resp.write(args[0])
            await resp.write_eof()
            return resp

        upstream.router.add_post("/v1/chat/completions", handler)
        self.upstream = TestServer(upstream)
        await self.upstream.start_server()
        self.cfg = GovernorConfig(
            upstream=str(self.upstream.make_url("/v1")), rate=1000.0, max_rate=1000.0, backoff_base_s=0.01, backoff_cap_s=0.05,
            cooldown_s=0.0, max_attempts=4, api_key_env="POLYMATH_TEST_KEY",
        )
        self.gov = Governor(self.cfg)
        self.client = TestClient(TestServer(self.gov.app()))
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        await self.client.close()
        await self.upstream.close()

    async def post(self, **body):
        return await self.client.post("/v1/chat/completions", json={"model": "m", **body}, headers={"Authorization": "Bearer k"})

    async def test_passes_through_success(self):
        r = await self.post()
        self.assertEqual(r.status, 200)
        self.assertEqual((await r.json())["choices"][0]["message"]["content"], "hi")
        self.assertEqual(self.seen[0]["path"], "/v1/chat/completions")
        self.assertEqual(self.seen[0]["auth"], "Bearer k")
        self.assertEqual(self.gov.stats.retries, 0)

    async def test_retries_429_and_decreases_rate_when_load_induced(self):
        self.gov.cfg.min_samples = 1
        self.script = [("json", 429, {"status": 429}, {"Retry-After": "0"}), ("json", 200, GOOD)]
        rate0 = self.gov.limiter_for('m').rate
        r = await self.post()
        self.assertEqual(r.status, 200)
        self.assertEqual(len(self.seen), 2)
        self.assertEqual(self.gov.stats.throttles, 1)
        self.assertLess(self.gov.limiter_for('m').rate, rate0 + self.cfg.alpha)  # decreased, then +alpha on success

    async def test_retries_overload_reported_inside_200_stream(self):
        self.script = [("sse", sse(OVERLOAD)), ("sse", sse(CHUNK, CHUNK2, "[DONE]"))]
        r = await self.post(stream=True)
        text = await r.text()
        self.assertEqual(r.status, 200)
        self.assertNotIn("overloaded", text)  # the failed attempt never reached the client
        self.assertIn('"hel"', text)
        self.assertIn("[DONE]", text)
        self.assertEqual(self.gov.stats.in_body_errors, 1)
        self.assertEqual(self.gov.stats.throttles, 1)

    async def test_retries_in_body_error_on_non_stream_200(self):
        self.script = [("json", 200, OVERLOAD), ("json", 200, GOOD)]
        r = await self.post()
        self.assertEqual((await r.json())["choices"][0]["message"]["content"], "hi")
        self.assertEqual(len(self.seen), 2)

    async def test_error_after_first_chunk_is_committed_not_retried(self):
        self.script = [("sse", sse(CHUNK, OVERLOAD))]
        r = await self.post(stream=True)
        text = await r.text()
        self.assertIn('"hel"', text)
        self.assertIn("overloaded", text)  # passed through: the client already consumed data
        self.assertEqual(len(self.seen), 1)

    async def test_idle_timeout_before_first_event_is_retried(self):
        self.gov.cfg.idle_timeout_s = 0.2
        self.script = [("sse_stall", sse(CHUNK, "[DONE]"), 1.0), ("sse", sse(CHUNK2, "[DONE]"))]
        r = await self.post(stream=True)
        text = await r.text()
        self.assertEqual(r.status, 200)
        self.assertIn('"lo"', text)
        self.assertNotIn('"hel"', text)
        self.assertEqual(self.gov.stats.connection_errors, 1)

    async def test_client_error_is_not_retried(self):
        self.script = [("json", 400, {"error": {"message": "bad request"}})]
        r = await self.post()
        self.assertEqual(r.status, 400)
        self.assertEqual(len(self.seen), 1)
        self.assertEqual(self.gov.stats.passed_through_errors, 1)

    async def test_gives_up_after_max_attempts_with_last_error(self):
        self.script = [("json", 503, {"error": "down"})] * 10
        r = await self.post()
        self.assertEqual(r.status, 503)
        self.assertEqual(len(self.seen), self.cfg.max_attempts)
        self.assertEqual(self.gov.stats.gave_up, 1)

    async def test_injects_key_only_when_client_sends_none(self):
        os.environ["POLYMATH_TEST_KEY"] = "envkey"
        try:
            await self.client.post("/v1/chat/completions", json={"model": "m"})
        finally:
            del os.environ["POLYMATH_TEST_KEY"]
        self.assertEqual(self.seen[0]["auth"], "Bearer envkey")

    async def test_request_abandoned_by_client_is_dropped_before_upstream(self):
        self.gov.limiter_for("m").rate = 0.5  # the second request waits ~2 s for its admission slot
        first = await self.post()
        self.assertEqual(first.status, 200)
        task = asyncio.ensure_future(self.client.post("/v1/chat/completions", json={"model": "m"}, headers={"Authorization": "Bearer k"}))
        await asyncio.sleep(0.3)
        task.cancel()  # the client gives up while its request is queued
        with self.assertRaises(asyncio.CancelledError):
            await task
        await asyncio.sleep(2.5)
        # The property that matters: an abandoned request never spends an upstream attempt. Depending on
        # the server configuration, aiohttp either cancels the handler itself or the governor's own check
        # (stats.abandoned) drops it before the attempt.
        self.assertEqual(len(self.seen), 1)
        self.assertEqual(self.gov.stats.requests, 2)
        self.assertEqual(self.gov.stats.attempts, 1)

    async def test_throttling_on_one_model_does_not_slow_another(self):
        self.gov.cfg.min_samples = 1
        self.script = [("json", 429, {"status": 429}), ("json", 200, GOOD), ("json", 200, GOOD)]
        await self.post()  # model "m": throttled once, then succeeds
        await self.client.post("/v1/chat/completions", json={"model": "other"}, headers={"Authorization": "Bearer k"})
        self.assertLess(self.gov.limiter_for("m").rate, self.cfg.rate + self.cfg.alpha)
        self.assertEqual(self.gov.limiter_for("other").rate, self.cfg.rate)  # never cut by m's throttling (already at max)
        s = await (await self.client.get("/__governor/stats")).json()
        self.assertEqual(set(s["models"]), {"m", "other"})

    async def test_stats_endpoint(self):
        await self.post()
        s = await (await self.client.get("/__governor/stats")).json()
        self.assertEqual(s["requests"], 1)
        self.assertEqual(s["completed"], 1)
        self.assertIn("rate_rps", s)


@unittest.skipIf(web is None, "aiohttp not installed")
class AimdLimiterTest(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.lim = AimdLimiter(GovernorConfig(rate=2.0, min_rate=0.5, max_rate=3.0, alpha=0.5, beta=0.5, cooldown_s=10.0, throttle_window=10, min_samples=4, decrease_above=0.35), clock=lambda: self.now)

    def test_fifo_slots_are_spaced_at_rate(self):
        slots = [self.lim.reserve() for _ in range(3)]
        self.assertEqual(slots, [100.0, 100.5, 101.0])

    def test_background_throttling_does_not_cut_the_rate(self):
        # Measured on NIM: 429s at a steady low fraction regardless of our load. Only backoff, no rate cut.
        for _ in range(4):
            for _ in range(4):
                self.lim.on_success()
            self.lim.rate = 2.0
            self.lim.on_throttle(retry_after=30.0)
        self.assertLess(self.lim.throttle_fraction, 0.35)
        self.assertEqual(self.lim.decreases, 0)
        self.assertEqual(self.lim.rate, 2.0)
        self.assertLess(self.lim.reserve(), 101.0)  # and no global pause either

    def test_burst_of_throttles_is_one_congestion_signal(self):
        for _ in range(5):
            self.lim.on_throttle()
        self.assertTrue(self.lim.load_induced)
        self.assertEqual(self.lim.rate, 1.0)
        self.assertEqual(self.lim.decreases, 1)
        self.now += 10.0
        self.lim.on_throttle()
        self.assertEqual(self.lim.rate, 0.5)  # floor
        self.lim.on_throttle()
        self.assertEqual(self.lim.rate, 0.5)

    def test_additive_increase_is_capped(self):
        for _ in range(10):
            self.lim.on_success()
        self.assertEqual(self.lim.rate, 3.0)

    def test_retry_after_pauses_everyone_when_load_induced(self):
        for _ in range(4):
            self.lim.on_throttle(retry_after=30.0)
        self.assertGreaterEqual(self.lim.reserve(), 130.0)


class HelpersTest(unittest.TestCase):
    @unittest.skipIf(web is None, "aiohttp not installed")
    def test_in_body_error(self):
        self.assertEqual(in_body_error(json.dumps(OVERLOAD).encode()), "Service temporarily overloaded")
        self.assertEqual(in_body_error(b'{"error": {"message": "x"}}'), "x")
        self.assertIsNone(in_body_error(json.dumps(GOOD).encode()))
        self.assertIsNone(in_body_error(b"not json"))

    @unittest.skipIf(web is None, "aiohttp not installed")
    def test_parse_retry_after(self):
        self.assertEqual(parse_retry_after("3"), 3.0)
        self.assertIsNone(parse_retry_after(None))
        self.assertIsNone(parse_retry_after("soon"))


if __name__ == "__main__":
    unittest.main()
