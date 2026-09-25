# ADR-006 — Standard-library-only Python core

**Status:** Accepted · **Date:** 2026-09-24

## Decision
The harness depends on nothing outside CPython ≥ 3.11: HTTP via `urllib` (proxy and CA aware through environment variables), JSON Schema subset validator, BM25 memory, TOML config via `tomllib`, tests via `unittest`.

## Alternatives considered
`httpx`/`openai` SDK, `pydantic`, `jsonschema`, `pytest`, OTel SDK — each individually reasonable, together a dependency and supply-chain surface disproportionate to what the harness needs.

## Consequences
+ Installs anywhere Python runs (incl. locked-down sandboxes); nothing to pin or audit.
− No connection pooling or HTTP/2 (irrelevant next to multi-second model latency); no streaming in v1 (roadmap).
