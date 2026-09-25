# ADR-012 — Govern the account's rate below every SDK, not inside each client

**Status:** Accepted · **Date:** 2026-09-25 · Research: [R4](../research/04-egress-governor.md) · Code: `polymath/egress/`

## Context
- **Measured losses.** 9 of 28 tasks in the v1.2 GLM-5.3 core run died of HTTP 429 after seven client-side retries each. The first multi-stack bake-off lost tasks the same way. On top of that, deepagents reported runs as `completed` whose final message was an exhausted-retry error ([R1](../research/01-sdk-landscape.md) D3).
- **Errors inside a 200.** NVIDIA NIM also signals overload *inside* a 200 stream (`data: {"error": "Service temporarily overloaded"}`). HTTP-status retry layers never see it, and it killed Pydantic AI runs (D5).
- **Per-client retries can't fix either.** The limit is per account, while every SDK retries per client. N independent backoffs still collide, and a status-based retrier can't see an in-body error.

## Decision
Run an OpenAI-compatible reverse proxy, the **egress governor**, between every agent stack and the provider:
- FIFO token-bucket admission at a rate adjusted by **loss-tolerant** AIMD: one decrease per congestion episode (a cooldown window), and only when the recent throttle fraction shows the throttling is ours (≥ 35 % of the last 40 attempts). A global pause on `Retry-After` applies in that case only. Plain AIMD was tried first and failed on real data: it drove the rate to 4 requests/min against provider-side throttling that didn't respond to our load ([R4 §4](../research/04-egress-governor.md)).
- Retry before commit: 429, 5xx, connection errors, an idle timeout before the first byte, and an error as the first SSE event or the body of a 200 are all retried while nothing has reached the client. After the first forwarded byte, errors pass through untouched.
- The last real error is returned when attempts or the deadline run out. An unretryable in-body error becomes an explicit 503.
- Clients change only `base_url` (`POLYMATH_BASE_URL`).

## Alternatives considered
- **Tune each SDK's retries.** Four configuration dialects. None can see NIM's in-body error at the HTTP layer, and none coordinates with the others.
- **Lower concurrency by hand.** It hides the problem at the cost of throughput, needs re-tuning per account and model, and does nothing about in-body errors.
- **An SDK-level model wrapper** (e.g. the `RequestRetryModel` equaliser in `evals/backends.py`). It fixes one SDK and still doesn't share the account's budget.

## Consequences
- \+ Every stack gets identical, fair infrastructure behaviour. This is a prerequisite for comparing stacks at all.
- \+ Failures become latency: under load, requests queue instead of dying.
- − One more process to run. The state is per process, so several machines sharing one account need one egress point or a shared bucket.
- − Deterministic in-body errors are retried until the attempt limit before they surface. Only transient ones have been observed on NIM.

## Evidence
16 tests (`tests/test_egress.py`) pin commit semantics, AIMD behaviour and key handling. Live, about 45 minutes into the four-stack bake-off: 8 concurrent agents, 306 requests, 56 throttle signals absorbed (23 of them in-body), **0 requests given up**, and no task lost to infrastructure.
