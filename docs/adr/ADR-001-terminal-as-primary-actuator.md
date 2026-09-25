# ADR-001 — A persistent terminal is the agent's primary actuator

**Status:** Accepted · **Date:** 2026-09-24

## Context
A generalist agent must be able to do "anything a developer can do on a computer". Options range from a curated catalogue of narrow tools to raw shell access. The question raised at project start: *should we add terminals?*

## Decision
Provide a **persistent bash session** (`bash` tool) as the primary actuator, complemented by a few structured tools where structure clearly beats the shell (file read/edit with line numbers and exact-match semantics, bounded search, and the control tools the harness must observe: `todo`, `notes`, `finish`, `delegate`).

## Alternatives considered
| Alternative | Why rejected |
|---|---|
| Curated tools only (run_tests, git_commit, http_get, …) | Coverage is bounded by what was anticipated; every new need is a code change; tool catalogues bloat the prompt. |
| Stateless `subprocess.run` per command | Loses cwd/env/venv/functions; models then mis-handle state and prefix every command with setup. |
| PTY-based terminal emulation | Enables TUIs agents shouldn't use; ANSI/redraw noise; harder completion detection. |
| Python-only code execution sandbox | Excludes the OS toolchain (git, tar, curl, compilers); models are at least as fluent in shell. |

## Consequences
+ One tool covers the long tail; code-mode (scripts instead of many tool calls) keeps context small.
+ Verification is natural (run the tests).
− Completion detection, timeouts, runaway output and wedged shells must be engineered (see [07](../07-terminal-subsystem.md)).
− Linux-specific process tracking (`/proc`) in v1.

## Evidence
43 % of all tool calls in the v1 GLM-5.3 run were `bash`; all 28 core tasks passed. 10 adversarial terminal scenarios are covered by unit tests, including background-process survival across timeouts and shell replacement after a builtin busy-loop.
