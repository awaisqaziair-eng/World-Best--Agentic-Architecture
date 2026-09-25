"""Protocols, HTTP client resilience, fail-over, record/replay."""

from __future__ import annotations

import json
import unittest

from polymath.gateway.base import ALL_FAILED, AUTH, BAD_REQUEST, CONTEXT_OVERFLOW, RETRY_EXHAUSTED, ChatRequest, ModelError
from polymath.gateway.openai_compat import OpenAICompatClient, TransportError
from polymath.gateway.protocols import NativeProtocol, TextProtocol, parse_text_tool_calls, split_reasoning
from polymath.gateway.resilience import CircuitBreaker, FallbackClient
from polymath.gateway.testing import RecordingClient, ReplayClient, ReplayDivergence, ScriptedClient, make_response
from polymath.types import Message, ToolCall

from .helpers import FakeOpenAIServer, TempDirTest, wire_call

TOOLS = [{"type": "function", "function": {"name": "bash", "description": "run", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}}}}]


def ok_body(msg: dict, model: str = "m") -> bytes:
    return json.dumps({"model": model, "choices": [{"message": {"role": "assistant", **msg}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 2}}).encode()


class Seq:
    """Transport returning a fixed sequence of (status, headers, body) or raising."""

    def __init__(self, items):
        self.items = list(items)
        self.bodies: list[dict] = []

    def __call__(self, url, headers, body, timeout):
        self.bodies.append(json.loads(body))
        it = self.items.pop(0)
        if isinstance(it, Exception):
            raise it
        return it


def client(transport, **kw) -> tuple[OpenAICompatClient, list[float]]:
    sleeps: list[float] = []
    c = OpenAICompatClient("m", base_url="http://x/v1", api_key="k", transport=transport, sleep=sleeps.append, **kw)
    return c, sleeps


REQ = ChatRequest(messages=[Message("user", "hi")], tools=TOOLS)


class TestProtocols(unittest.TestCase):
    def test_split_reasoning(self):
        self.assertEqual(split_reasoning("<think>hmm</think>Answer"), ("Answer", "hmm"))
        self.assertEqual(split_reasoning("reasoning only</think>Final"), ("Final", "reasoning only"))
        self.assertEqual(split_reasoning("plain"), ("plain", None))

    def test_native_decode_repairs_and_ids(self):
        raw = {
            "content": "<think>plan</think>ok",
            "tool_calls": [
                {"id": "", "function": {"name": "bash", "arguments": '{"command": "ls",}'}},
                {"id": "dup", "function": {"name": "bash", "arguments": "{bad json"}},
                {"id": "dup", "function": {"name": "bash", "arguments": {"command": "pwd"}}},
            ],
        }
        content, calls, reasoning = NativeProtocol().decode(raw, salt="s")
        self.assertEqual(content, "ok")
        self.assertEqual(reasoning, "plan")
        self.assertEqual(calls[0].arguments, {"command": "ls"})
        self.assertIsNotNone(calls[1].parse_error)
        self.assertEqual(calls[2].arguments, {"command": "pwd"})
        self.assertEqual(len({c.id for c in calls}), 3)
        self.assertTrue(all(c.id for c in calls))

    def test_native_decode_text_smuggled_calls(self):
        raw = {"content": 'Let me look.\n<tool_call>{"name": "bash", "arguments": {"command": "ls"}}</tool_call>'}
        content, calls, _ = NativeProtocol().decode(raw, salt="s")
        self.assertEqual([c.name for c in calls], ["bash"])
        self.assertEqual(content, "Let me look.")

    def test_native_encode_merges_users_and_pairs_tools(self):
        msgs = [
            Message("system", "S"),
            Message("user", "a"),
            Message("user", "b"),
            Message("assistant", None, tool_calls=[ToolCall("c1", "bash", {"command": "ls"})]),
            Message("tool", "out", tool_call_id="c1", name="bash"),
        ]
        wire, extra = NativeProtocol().encode(msgs, TOOLS)
        self.assertEqual([w["role"] for w in wire], ["system", "user", "assistant", "tool"])
        self.assertEqual(wire[1]["content"], "a\n\nb")
        self.assertEqual(json.loads(wire[2]["tool_calls"][0]["function"]["arguments"]), {"command": "ls"})
        self.assertIn("tools", extra)

    def test_text_protocol_roundtrip(self):
        tp = TextProtocol()
        msgs = [
            Message("system", "S"),
            Message("user", "task"),
            Message("assistant", None, tool_calls=[ToolCall("c1", "bash", {"command": "ls"}), ToolCall("c2", "bash", {"command": "pwd"})]),
            Message("tool", "o1", tool_call_id="c1", name="bash"),
            Message("tool", "o2", tool_call_id="c2", name="bash"),
        ]
        wire, extra = tp.encode(msgs, TOOLS)
        self.assertEqual(extra, {})
        self.assertIn("# Tool calling protocol", wire[0]["content"])
        self.assertIn("<tool_call>", wire[2]["content"])
        self.assertEqual(wire[3]["role"], "user")
        self.assertEqual(wire[3]["content"].count("<tool_result"), 2)
        _, calls, _ = tp.decode({"content": wire[2]["content"]}, salt="z")
        self.assertEqual([c.arguments["command"] for c in calls], ["ls", "pwd"])

    def test_function_tag_format(self):
        calls = parse_text_tool_calls('<function=read_file>{"path": "a.txt"}</function>')
        self.assertEqual((calls[0].name, calls[0].arguments), ("read_file", {"path": "a.txt"}))


class TestHTTPClient(unittest.TestCase):
    def test_retry_then_success_honours_retry_after(self):
        t = Seq([(429, {"retry-after": "2"}, b"slow down"), (503, {}, b"x"), TransportError("timeout"), (200, {}, ok_body({"content": "hi"}))])
        c, sleeps = client(t)
        r = c.complete(REQ)
        self.assertEqual(r.content, "hi")
        self.assertEqual(r.attempts, 4)
        self.assertEqual(sleeps[0], 2.0)
        self.assertEqual(len(sleeps), 3)

    def test_backoff_is_bounded_and_growing(self):
        c, _ = client(Seq([]), retry_base_s=1.0, retry_cap_s=8.0)
        for attempt in range(1, 10):
            d = c.backoff_delay(attempt)
            self.assertLessEqual(d, 8.0)
            self.assertGreaterEqual(d, 0.25 * min(8.0, 2 ** (attempt - 1)))

    def test_retry_exhausted(self):
        c, sleeps = client(Seq([(500, {}, b"boom")] * 4), max_retries=3)
        with self.assertRaises(ModelError) as cm:
            c.complete(REQ)
        self.assertEqual(cm.exception.kind, RETRY_EXHAUSTED)
        self.assertEqual(len(sleeps), 3)

    def test_malformed_200_is_retried(self):
        c, _ = client(Seq([(200, {}, b"<html>"), (200, {}, b'{"choices": []}'), (200, {}, ok_body({"content": "ok"}))]))
        self.assertEqual(c.complete(REQ).content, "ok")

    def test_context_overflow_detected(self):
        c, sleeps = client(Seq([(400, {}, b'{"error": "This model\'s maximum context length is 8192 tokens"}')]))
        with self.assertRaises(ModelError) as cm:
            c.complete(REQ)
        self.assertEqual(cm.exception.kind, CONTEXT_OVERFLOW)
        self.assertEqual(sleeps, [])

    def test_auth_not_retried(self):
        c, sleeps = client(Seq([(401, {}, b"bad key")]))
        with self.assertRaises(ModelError) as cm:
            c.complete(REQ)
        self.assertEqual(cm.exception.kind, AUTH)
        self.assertEqual(sleeps, [])

    def test_auto_downgrade_to_text_protocol(self):
        t = Seq([(400, {}, b'{"error": "tool_choice/tools is not supported for this model"}'), (200, {}, ok_body({"content": '<tool_call>{"name":"bash","arguments":{"command":"ls"}}</tool_call>'}))])
        c, _ = client(t)
        r = c.complete(REQ)
        self.assertEqual(c.protocol, "text")
        self.assertEqual(r.protocol, "text")
        self.assertNotIn("tools", t.bodies[1])
        # REQ has no system message: the protocol must inject one carrying the tool instructions.
        self.assertEqual(t.bodies[1]["messages"][0]["role"], "system")
        self.assertIn("Tool calling protocol", t.bodies[1]["messages"][0]["content"])
        self.assertEqual(r.tool_calls[0].arguments, {"command": "ls"})

    def test_generic_400_is_bad_request(self):
        c, _ = client(Seq([(400, {}, b"invalid temperature")]), protocol="native")
        with self.assertRaises(ModelError) as cm:
            c.complete(REQ)
        self.assertEqual(cm.exception.kind, BAD_REQUEST)

    def test_usage_and_cached_tokens(self):
        body = json.dumps({"choices": [{"message": {"content": "x"}}], "usage": {"prompt_tokens": 100, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 80}}}).encode()
        c, _ = client(Seq([(200, {}, body)]))
        u = c.complete(REQ).usage
        self.assertEqual((u.input_tokens, u.output_tokens, u.cached_tokens), (100, 5, 80))


def sse(*chunks: dict, done: bool = True) -> list[bytes]:
    out = [b": keep-alive\n"]
    for c in chunks:
        out += [b"data: " + json.dumps(c).encode() + b"\n", b"\n"]
    if done:
        out.append(b"data: [DONE]\n")
    return out


class TestStreaming(unittest.TestCase):
    def test_assembles_content_reasoning_and_split_tool_args(self):
        chunks = sse(
            {"id": "x", "model": "m", "choices": [{"delta": {"reasoning_content": "think "}}]},
            {"choices": [{"delta": {"reasoning_content": "more"}}]},
            {"choices": [{"delta": {"content": "Running "}}]},
            {"choices": [{"delta": {"content": "it."}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "bash", "arguments": '{"comm'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'and": "ls"}'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 1, "id": "c2", "function": {"name": "read_file", "arguments": '{"path": "a"}'}}]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 50, "completion_tokens": 7}},
        )
        c, _ = client(Seq([(200, {}, iter(chunks))]), stream=True)
        r = c.complete(REQ)
        self.assertEqual((r.content, r.reasoning, r.finish_reason), ("Running it.", "think more", "tool_calls"))
        self.assertEqual([(t.id, t.name, t.arguments) for t in r.tool_calls], [("c1", "bash", {"command": "ls"}), ("c2", "read_file", {"path": "a"})])
        self.assertEqual((r.usage.input_tokens, r.usage.output_tokens), (50, 7))

    def test_request_body_asks_for_stream_and_usage(self):
        t = Seq([(200, {}, iter(sse({"choices": [{"delta": {"content": "ok"}}]})))])
        c, _ = client(t, stream=True)
        c.complete(REQ)
        self.assertTrue(t.bodies[0]["stream"])
        self.assertEqual(t.bodies[0]["stream_options"], {"include_usage": True})

    def test_stream_options_rejection_is_sticky_fallback(self):
        t = Seq([(400, {}, b'{"error": "unknown field stream_options"}'), (200, {}, iter(sse({"choices": [{"delta": {"content": "ok"}}]}))), (200, {}, iter(sse({"choices": [{"delta": {"content": "ok2"}}]})))])
        c, sleeps = client(t, stream=True)
        self.assertEqual(c.complete(REQ).content, "ok")
        self.assertNotIn("stream_options", t.bodies[1])
        c.complete(REQ)
        self.assertNotIn("stream_options", t.bodies[2])
        self.assertEqual(sleeps, [])

    def test_broken_stream_is_retried(self):
        def broken():
            yield from sse({"choices": [{"delta": {"content": "par"}}]}, done=False)
            raise TransportError("stream interrupted: connection reset")

        t = Seq([(200, {}, broken()), (200, {}, iter(sse({"choices": [{"delta": {"content": "full"}}]})))])
        c, sleeps = client(t, stream=True)
        r = c.complete(REQ)
        self.assertEqual((r.content, r.attempts), ("full", 2))
        self.assertEqual(len(sleeps), 1)

    def test_error_event_and_empty_stream_are_retried(self):
        t = Seq([(200, {}, iter(sse({"error": {"message": "overloaded"}}))), (200, {}, iter([b"data: [DONE]\n"])), (200, {}, iter(sse({"choices": [{"delta": {"content": "ok"}}]})))])
        c, _ = client(t, stream=True)
        self.assertEqual(c.complete(REQ).attempts, 3)

    def test_progress_callback_fires_and_cannot_break_request(self):
        import itertools

        ticks: list[tuple[int, float]] = []
        clock = itertools.count(0.0, 1.5)  # each chunk "takes" 1.5 s of monotonic time

        def slow_lines():
            for c in sse(*[{"choices": [{"delta": {"content": "x"}}]} for _ in range(6)]):
                yield c

        c, _ = client(Seq([(200, {}, slow_lines())]), stream=True)
        import polymath.gateway.openai_compat as oc

        real = oc.time.monotonic
        oc.time.monotonic = lambda: next(clock)
        try:
            def cb(n, s):
                ticks.append((n, s))
                raise RuntimeError("renderer bug")  # must be swallowed

            r = c.complete(ChatRequest(messages=[Message("user", "hi")], on_progress=cb))
        finally:
            oc.time.monotonic = real
        self.assertEqual(r.content, "xxxxxx")
        self.assertGreaterEqual(len(ticks), 2)
        self.assertEqual(ChatRequest(messages=[Message("user", "hi")], on_progress=cb).fingerprint(), ChatRequest(messages=[Message("user", "hi")]).fingerprint())

    def test_sse_bytes_and_plain_json_both_accepted(self):
        body = b"".join(sse({"choices": [{"delta": {"content": "from bytes"}}]}))
        c, _ = client(Seq([(200, {}, body), (200, {}, ok_body({"content": "plain"}))]), stream=True)
        self.assertEqual(c.complete(REQ).content, "from bytes")
        self.assertEqual(c.complete(REQ).content, "plain")  # server ignored stream=true


class Failing:
    def __init__(self, model, kind):
        self.model, self.kind, self.calls = model, kind, 0

    def complete(self, req):
        self.calls += 1
        raise ModelError("fail", kind=self.kind, model=self.model)


class TestFallback(unittest.TestCase):
    def test_failover_and_breaker(self):
        now = [0.0]
        primary = Failing("p", RETRY_EXHAUSTED)
        backup = ScriptedClient([make_response("from backup")] * 5, model="b")
        fc = FallbackClient([primary, backup], failure_threshold=2, cooldown_s=60, clock=lambda: now[0])
        for _ in range(3):
            self.assertEqual(fc.complete(REQ).content, "from backup")
        self.assertEqual(primary.calls, 2)  # breaker opened after 2 failures; third request skipped it
        now[0] = 61.0  # half-open: primary gets one trial again
        fc.complete(REQ)
        self.assertEqual(primary.calls, 3)

    def test_bad_request_is_not_failed_over(self):
        backup = ScriptedClient([make_response("x")], model="b")
        fc = FallbackClient([Failing("p", BAD_REQUEST), backup])
        with self.assertRaises(ModelError) as cm:
            fc.complete(REQ)
        self.assertEqual(cm.exception.kind, BAD_REQUEST)
        self.assertEqual(backup.requests, [])

    def test_all_failed(self):
        fc = FallbackClient([Failing("a", RETRY_EXHAUSTED), Failing("b", AUTH)])
        with self.assertRaises(ModelError) as cm:
            fc.complete(REQ)
        self.assertEqual(cm.exception.kind, ALL_FAILED)
        self.assertEqual(len(cm.exception.causes), 2)

    def test_all_breakers_open_still_tries_soonest(self):
        now = [0.0]
        a = Failing("a", RETRY_EXHAUSTED)
        fc = FallbackClient([a], failure_threshold=1, cooldown_s=100, clock=lambda: now[0])
        with self.assertRaises(ModelError):
            fc.complete(REQ)
        with self.assertRaises(ModelError):
            fc.complete(REQ)
        self.assertEqual(a.calls, 2)

    def test_breaker_states(self):
        now = [0.0]
        b = CircuitBreaker(2, 10, clock=lambda: now[0])
        b.record_failure()
        self.assertEqual(b.state, "closed")
        b.record_failure()
        self.assertEqual(b.state, "open")
        now[0] = 10
        self.assertEqual(b.state, "half_open")
        b.record_success()
        self.assertEqual(b.state, "closed")


class TestRecordReplay(TempDirTest):
    def test_record_then_strict_replay_and_divergence(self):
        inner = ScriptedClient([make_response("one"), make_response("two")])
        rec = RecordingClient(inner, self.tmp / "cassette.jsonl")
        r1 = ChatRequest(messages=[Message("user", "a")])
        r2 = ChatRequest(messages=[Message("user", "b")])
        rec.complete(r1)
        rec.complete(r2)
        rp = ReplayClient(self.tmp / "cassette.jsonl", strict=True)
        self.assertEqual(rp.complete(r1).content, "one")
        with self.assertRaises(ReplayDivergence):
            rp.complete(ChatRequest(messages=[Message("user", "DIFFERENT")]))

    def test_fake_server_end_to_end_through_client(self):
        srv = FakeOpenAIServer([{"content": None, "tool_calls": [wire_call("bash", {"command": "ls"}, "c1")]}])
        c, _ = client(srv)
        r = c.complete(REQ)
        self.assertEqual(r.tool_calls[0].name, "bash")
        self.assertEqual(r.usage.input_tokens, 1000)


if __name__ == "__main__":
    unittest.main()
