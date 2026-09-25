# ADR-008 — Exact-string file edits with closest-match errors

**Status:** Accepted · **Date:** 2026-09-24

## Decision
`edit_file(path, old_string, new_string, replace_all=false)`: `old_string` must occur exactly once (or `replace_all`). On a miss, return the most similar region (difflib ratio ≥ 0.6) with line numbers. Never apply fuzzily.

## Alternatives considered
Line-range edits (drift after the first edit), unified diffs (context mismatches, hunk-header errors), full-file rewrites (token waste, silent deletions), fuzzy matching (silent wrong edits).

## Consequences
+ Deterministic, reviewable edits; one-step recovery from misses.
− The model must reproduce whitespace exactly (mitigated by line-numbered reads and the closest-match hint).
