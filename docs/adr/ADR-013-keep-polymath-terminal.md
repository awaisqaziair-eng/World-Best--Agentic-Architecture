# ADR-013 — Keep Polymath's terminal in v2; replace the prebuilt shells with it

**Status:** Accepted · **Date:** 2026-09-25 · Research: [R3](../research/03-terminal-bakeoff.md) · Code: `polymath/v2/terminal.py` · Refines [ADR-001](ADR-001-terminal-as-primary-actuator.md)

## Context
v2 builds on prebuilt SDKs, and each ships a shell tool. A terminal is the agent's primary actuator (ADR-001), so its failure modes decide whether work is lost silently. R3 drove six shell implementations through fourteen adversarial scenarios, after auditing the benchmark itself and correcting four bugs in it that had penalised competitors.

## Decision
The v2 agent uses Polymath's `PersistentShell` wrapped as a native Pydantic AI toolset (`bash`). It replaces the harness's Coder `shell`, and the ablation flag `terminal=False` swaps Coder's shell back in.

Adopted from the competitors: `background=true` starts a job **inside the same persistent shell**, so it inherits the cwd, virtualenv and environment, and returns its PID and log path immediately. This takes Coder's handle idea and removes its main drawback: Coder's detached processes start in the initial cwd with no shell state.

## Alternatives considered
| Shell | R3 score | Deciding failure |
|---|---|---|
| Pydantic AI Coder `shell` | 10/14 | No state between calls; timed-out commands keep running forever (leaked busy loops) |
| Pydantic AI `Shell` `run_command` | 10/14 | No env/function persistence; `read` blocks until timeout |
| deepagents `LocalShellBackend` | 9/14 | Head-first truncation loses the tail; timeouts orphan children |
| LangChain `ShellSession` | 7/14 | Restart on every timeout: 10 s penalty, background jobs killed, partial output discarded |
| OpenAI Agents SDK `exec_command` | 7/14 | Commands containing `&` run in `/` (defect D1); output unbounded by default |

## Consequences
- \+ The only design with 14/14. The same code serves v1 and v2 (`render_shell_result` is shared).
- − Not interactive: stdin is `/dev/null` by design. A PTY session tool (the Agents SDK's idea) is the recorded option if interactive programs become a requirement.
- − We maintain the terminal ourselves instead of inheriting upstream fixes. It is ~350 lines with its own test suite.

## Evidence
R3 table and raw data (`research/results/terminal_bakeoff.json`). The ablation arm `polymath-v2-terminal` against `pydanticai-coder` measures the effect on real tasks ([R5](../research/README.md)).
