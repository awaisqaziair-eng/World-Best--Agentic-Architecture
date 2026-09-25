# ADR-010 — Stream every model call; time out on idleness, not duration

**Status:** Accepted · **Date:** 2026-09-25 · Supersedes the v1 non-streaming transport

## Context (found live, not in design review)
v1 used non-streaming requests with a 240 s socket timeout. Two measurements exposed a failure cascade:
1. GLM-5.3 generates ~62 tokens/s and can spend its entire output budget reasoning (6,000 tokens → 0 characters of content in 97 s). A 16k-token turn therefore takes ~265 s — longer than the timeout.
2. In the hard tier, the two generation-heavy tasks (`hard-expr-evaluator`, `hard-markdown`) sat for > 10 minutes with **zero** journaled responses in *both* harness modes: each attempt timed out before the first byte, and the retry restarted the generation. Because a client-side timeout does not cancel server-side generation, retries stacked concurrent generations on the account; a concurrently started 28-task run produced no results in ~15 minutes.

## Decision
- `stream: true` (SSE) for every request, with `stream_options.include_usage` (sticky fallback if a server rejects it).
- `request_timeout_s` (240 s) becomes an **idle** timeout — the socket timeout of each read — so a generation that keeps producing tokens never trips it; `max_request_s` (900 s) caps total duration.
- Deltas are assembled into one `ModelResponse` (content, reasoning, tool calls merged by `index`), so the kernel and the journal are unchanged (invariant I1 holds: only the final response is journaled).
- Mid-stream failures (reset, error event, empty stream) are transient errors and retried.
- Per-model `max_output_tokens` (`model_params`), 32k for GLM-5.3.

## Consequences
+ Long generations complete; closing a stream aborts server-side work instead of orphaning it.
+ Works with servers that ignore `stream` (plain JSON bodies are still accepted).
− Slightly more parsing code (covered by 6 streaming tests: split tool arguments, usage chunk, keep-alive comments, `stream_options` rejection, mid-stream breaks, error events).

## Evidence
Live on NIM with a deliberately short 30 s idle timeout: tool calls assembled from deltas on GLM-5.3 and Nemotron-3-Super with usage reported; a 97 s GLM-5.3 generation and a 19.7 s Nemotron generation completed in one attempt. The v1.2 re-run of the hard tier is reported in [RESULTS](../evaluation/RESULTS.md).
