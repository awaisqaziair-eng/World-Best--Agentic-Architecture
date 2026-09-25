# Architecture Decision Records

| ADR | Decision | Status |
|---|---|---|
| [ADR-001](ADR-001-terminal-as-primary-actuator.md) | A persistent terminal is the agent's primary actuator | Accepted |
| [ADR-002](ADR-002-event-sourced-session.md) | The session is an append-only event log; context is a projection | Accepted |
| [ADR-003](ADR-003-determinism-boundary.md) | Deterministic harness, stochastic leaves; workflows for fixed procedures | Accepted |
| [ADR-004](ADR-004-single-agent-default.md) | Single agent by default; sub-agents on demand via `delegate` | Accepted |
| [ADR-005](ADR-005-dual-tool-protocols.md) | Native tool calling with automatic text-protocol fallback | Accepted |
| [ADR-006](ADR-006-stdlib-only-core.md) | Standard-library-only Python core | Accepted |
| [ADR-007](ADR-007-explicit-finish-and-verification.md) | Explicit `finish` + verification loop; accept final-looking prose | Accepted (amended v1.1) |
| [ADR-008](ADR-008-exact-string-edits.md) | Exact-string file edits with closest-match errors | Accepted |
| [ADR-009](ADR-009-measured-harness-overhead.md) | Harness features must pay for their tokens (v1.1 overhead reductions) | Accepted |

Format: Context → Decision → Alternatives considered → Consequences → Evidence.
