# 01 — Product Requirements

> Status: **Baseline v1.0** · Traceability: each requirement lists the code and the test that satisfies it.

## 1. Users and jobs-to-be-done

| Persona | Job | Typical request |
|---|---|---|
| **Developer** | Offload well-specified engineering work | "Fix the failing tests", "add a `--json` flag", "optimise this function" |
| **Analyst** | Get exact answers from data | "Revenue per region from this CSV, skipping bad rows" |
| **Operator** | Automate terminal chores | "Package the release and write its checksum", "find the IPs causing 5xx" |
| **Researcher / writer** | Synthesise documents into deliverables | "Which team owns X and who leads it?", "Write the API reference" |
| **Pipeline / parent agent** | Call an agent as a component | CI job, workflow step, or `delegate` from another agent |

## 2. Capability matrix

| Capability | How it is delivered | Eval coverage |
|---|---|---|
| Write, run and test code | `bash`, `write_file`, `edit_file`, skills `python-engineering` | code-lru-cache, code-intervals-tests, code-cli-todo |
| Optimise performance | terminal benchmarking + edit loop | code-optimize |
| Debug and refactor | `grep`, `read_file`, `edit_file`, skill `debugging` | debug-inventory, debug-crash-hardening, refactor-rename |
| Terminal operations | persistent `bash` | ops-log-forensics, ops-organize-files, ops-archive-checksum, ops-config-audit |
| Services & networking | background processes, `curl`, skill `web-services` | ops-http-service |
| Version control | `bash` + skill `git-workflow` | git-feature-flow |
| Data analysis | Python stdlib via `bash`, skill `data-analysis` | data-sales-report, data-sqlite-analytics, data-json-flatten |
| Visualisation | generated SVG | data-svg-chart |
| Math & reasoning | code-backed computation | math-digit-sum, math-grid-paths, reason-schedule |
| Writing under constraints | `write_file` + mechanical self-checks | write-api-docs, write-exec-summary |
| Information extraction | parsing + normalisation | extract-invoices |
| Research over documents | `grep`/`glob`/`read_file`, `notes`, `delegate` | research-multihop, research-parallel |
| Web retrieval | `fetch_url` | web-local-docs |
| Knowledge Q&A | direct answer | qa-knowledge |
| Ambiguity handling | assumption + disclosure | general-ambiguous |

## 3. Functional requirements

| ID | Requirement | Implementation | Verified by |
|---|---|---|---|
| FR-01 | Accept a task: instruction, workspace, optional acceptance criteria, verify command, budget | `TaskSpec`, `Runtime.run` | `test_kernel.test_happy_path` |
| FR-02 | Act through a persistent shell whose cwd/env persist across calls | `PersistentShell` | `test_tools.TestPersistentShell.*` |
| FR-03 | Read (paged), write and exact-edit files, with self-correcting error messages | `files.py` | `TestFileTools.*` |
| FR-04 | Search by filename glob and by content regex | `search.py` | `test_glob_and_grep` |
| FR-05 | Maintain an explicit plan and a durable scratchpad | `todo`, `notes` | `test_todo_and_notes` |
| FR-06 | Terminate explicitly via `finish(answer, artifacts)` | `FinishTool`, `_on_finish` | `test_happy_path` |
| FR-07 | Verify completion by command or criteria judge and feed failures back | `Verifier` | `test_verify_command_feedback_loop`, `test_judge_verification` |
| FR-08 | Delegate independent sub-tasks to parallel sub-agents with fresh contexts | `delegate`, `_spawn_subagents` | `test_delegation_runs_parallel_subagents` |
| FR-09 | Persist every event; resume an interrupted session; re-execute pending calls | `EventLog`, `Agent.resume` | `test_resume_executes_pending_calls`, `test_kill_dash_nine_then_resume` |
| FR-10 | Replay a session against recorded model outputs | `Runtime.replay`, `ReplayClient` | `test_replay_reproduces_run` |
| FR-11 | Keep the prompt within the model window at all times | `ContextEngine` | `test_context.*` |
| FR-12 | Enforce turn/token/time/tool-call budgets with a graceful wrap-up | `_budget_check`, `_wrap_up` | `test_turn_budget_wrap_up`, `test_budget_warning_at_80_percent` |
| FR-13 | Detect loops and error streaks and inject corrective guidance | `_health_checks` | `test_loop_detection_injects_guidance_once`, `test_error_streak_guidance` |
| FR-14 | Survive transient model/transport faults; fail over across models | `OpenAICompatClient`, `FallbackClient` | `TestHTTPClient.*`, `TestFallback.*`, `TestChaos.*` |
| FR-15 | Work with models lacking native tool calling | `TextProtocol`, auto-downgrade | `test_auto_downgrade_to_text_protocol` |
| FR-16 | Long-term memory across sessions | `MemoryStore`, `memory` tool | `TestMemory.*` |
| FR-17 | On-demand procedural knowledge (skills) | `load_skill`, `skills/*/SKILL.md` | live runs (skill loads visible in traces) |
| FR-18 | Deterministic workflows with journaled, resumable steps | `Workflow`, `Loop`, `Step` | `TestWorkflows.*` |
| FR-19 | Emit OTel-GenAI-shaped spans and a live console view | `Tracer`, `ConsoleRenderer` | `test_trace_spans_follow_genai_conventions` |
| FR-20 | CLI: run, chat, resume, replay, trace, sessions, models, workflow | `cli.py` | manual + smoke |

## 4. Non-functional requirements

| ID | Quality | Requirement | Status |
|---|---|---|---|
| NFR-01 | Reliability | A tool exception never terminates the run | ✅ `test_crashing_tool_is_captured` |
| NFR-02 | Reliability | A transport fault rate of 40% still completes a scripted task | ✅ `test_agent_survives_random_faults` |
| NFR-03 | Durability | Crash at any point loses at most the in-flight step | ✅ journal-before-act + torn-tail repair |
| NFR-04 | Termination | Every run ends within budget + 1 wrap-up turn | ✅ invariant I3 |
| NFR-05 | Portability | CPython ≥ 3.11 on Linux; no third-party packages | ✅ (terminal descendant tracking uses `/proc`: Linux) |
| NFR-06 | Performance | Harness overhead per turn ≪ model latency (target < 50 ms excl. tools) | ✅ projection + prepare are O(events) in-memory |
| NFR-07 | Memory safety | Unbounded tool output never exhausts memory | ✅ 20 MB flood test: capture bounded to ~4 MB |
| NFR-08 | Concurrency | Parallel sub-agents never corrupt the log or each other's shells | ✅ 8-thread append test; per-agent shell scratch |
| NFR-09 | Observability | Every model call and tool call is traceable with timing and tokens | ✅ |
| NFR-10 | Testability | Full offline suite runs in < 30 s | ✅ ~12 s, 122 tests |
| NFR-11 | Spec fidelity | Emitted artifacts validate against `spec/schemas` | ✅ `test_specs.py` |

## 5. Acceptance criteria for v1.0

1. All offline tests pass three consecutive runs with `ResourceWarning` as error. ✅
2. All 28 eval verifiers reject untouched workspaces and accept reference solutions. ✅ (`python -m evals.reference`)
3. Live evaluation on at least two NIM models with results published, including an ablation of the harness. See [evaluation/RESULTS.md](evaluation/RESULTS.md).
4. Every document in `docs/` describes implemented behaviour; nothing aspirational is presented as done (future work lives in [16-roadmap.md](16-roadmap.md)).
