# ADR-005 — Native tool calling with automatic text-protocol fallback

**Status:** Accepted · **Date:** 2026-09-24

## Context
OpenAI-compatible endpoints differ: some models/templates support `tools`, some reject them, some emit calls as text in `content`, some return invalid JSON arguments or missing ids.

## Decision
A canonical internal message format with two wire protocols: **native** (`tools`/`tool_calls`) and **text** (`<tool_call>{json}</tool_call>` in content, `<tool_result>` blocks back). In `auto` mode the client starts native and permanently downgrades a client to text if the endpoint rejects tools. Decoding is forgiving in both directions.

## Consequences
+ Any instruction-following model can drive the agent.
+ Smuggled text calls in native mode are recovered instead of lost.
− Two code paths to test (both covered: `TestProtocols`, `test_auto_downgrade_to_text_protocol`).

## Evidence
The downgrade test also exposed a real bug (tool instructions were dropped when a request had no system message), fixed before release.
