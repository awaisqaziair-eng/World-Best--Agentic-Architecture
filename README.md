# Polymath — a generalist agent, engineered end to end

Polymath is a working **all-round autonomous agent** and the **harness** that makes it reliable: give it a task in plain language and a workspace, and it writes and tests code, debugs, operates the terminal, analyses data, researches documents and the web, and produces written deliverables — then reports what it did and how it verified it.

Everything here is implemented, tested offline, and **evaluated live on NVIDIA NIM models with hidden verifiers**, including ablations that measure what each part of the harness is worth. Results — including the unflattering ones — are in [`docs/evaluation/RESULTS.md`](docs/evaluation/RESULTS.md).

```text
$ polymath run "Write primes.py that prints how many primes are below 1,000,000 and their sum; run it" -w ./work
━━ task Write primes.py that prints how many primes are below 1,000,000 and their sum; run it
  ▶ write_file primes.py (787 chars)
  ▶ bash python3 primes.py
    ✓ Traceback … ModuleNotFoundError: No module named 'numpy'
  numpy isn't installed — I'll switch to a pure standard-library sieve.
  ▶ write_file primes.py (860 chars)
  ▶ bash python3 primes.py
    ✓ Number of primes below 1000000: 78498
  ▶ bash python3 - <<'EOF'   (independent cross-check)
━━ completed (finished) · 7 turns · 7 tool calls · 33,028 tokens · 25.6s
78,498 primes; sum 37,550,402,023 — verified with an independent second sieve.
```

---

## Two generations, one evidence base

| | **v1** (`polymath/`, stdlib only) | **v2** (`polymath/v2/`, on the Pydantic AI harness) |
|---|---|---|
| Idea | Every layer built from scratch: loop, gateway, context engine, terminal, event log | A **prebuilt harness as shipped**, with Polymath components swapped in **only where measurement shows the prebuilt part falls short** |
| Agent loop, file tools, repo context, arg repair | Polymath | Pydantic AI `Coder`, **unmodified** |
| Terminal | Polymath `PersistentShell` | the same terminal, as a native toolset (14/14 adversarial scenarios vs best prebuilt 10/14, [R3](docs/research/03-terminal-bakeoff.md)) |
| Old tool results | batched clearing + compaction | **addressable eviction**: stubs with handles, exact recall, fault-driven pinning, reacquisition metrics ([R2](docs/research/02-context-management-literature.md), [ADR-014](docs/adr/ADR-014-recallable-eviction-and-ledger.md)) |
| State after compaction | model-written summary | + a **machine-derived state ledger** (files, commands, exit codes, tests) |
| Rate limits | per-client retries | **egress governor** below every SDK: loss-tolerant AIMD, retry before commit ([R4](docs/research/04-egress-governor.md)) |
| Run | `polymath run "…"` | `python -m polymath.v2 "…" --workspace DIR` |

v2 is the answer to "don't build your own SDK; use prebuilt ones and enhance them". It was chosen by **auditing four prebuilt SDKs in source** (10 reproducible defects, [R1](docs/research/01-sdk-landscape.md)) and **benchmarking them on the same tasks, model and hidden verifiers** (R5, running). Every swap has an off switch, so each claimed gain is an ablation, not an anecdote. Start at [docs/18-v2-architecture.md](docs/18-v2-architecture.md) and [docs/research](docs/research/README.md).

---

## Architecture at a glance

```mermaid
flowchart LR
    U([task]) --> K
    subgraph Harness
      K["Kernel loop\nbudgets · health checks · finish/verify · delegate"]
      C["Context engine\nproject → clear → compact"]
      G["Model gateway\nstreaming · retries · fail-over · protocols"]
      T["Tools (hands)\npersistent bash · files · search · plan/notes\nmemory · skills · web · delegate"]
      E[("Event log\nappend-only journal")]
    end
    K --> C --> E
    K --> G --> M[("Models\nNIM · vLLM · OpenAI")]
    K --> T --> OS[("Workspace + OS")]
    K --> E
    E -. "resume · replay · trace" .-> K
```

Five decisions define it ([02 — System architecture](docs/02-system-architecture.md)):

1. **Brain and hands are decoupled** — the loop only ever calls `execute(call) → result`.
2. **The session is an append-only event log**; the model's context is a pure projection of it → crash-resume, deterministic replay, audit and tracing from one mechanism.
3. **The terminal is the primary actuator** — a persistent, hardened bash session ([07](docs/07-terminal-subsystem.md), [ADR-001](docs/adr/ADR-001-terminal-as-primary-actuator.md)).
4. **Deterministic harness, stochastic leaves** — plus deterministic, journaled workflows for fixed procedures ([08](docs/08-determinism.md)).
5. **Context is a budget, engineered every turn**, with a cache-stable prefix ([05](docs/05-context-engineering.md)).

## Quickstart

```bash
python3 --version                       # 3.11+; no dependencies (standard library only)
export NVIDIA_NIM_API_KEY=nvapi-…        # or any OpenAI-compatible endpoint via POLYMATH_BASE_URL
python3 -m polymath run "Summarise report.txt into summary.md in under 150 words" -w ./work
python3 -m polymath chat -w ./work       # interactive
python3 -m polymath trace <session>      # span tree; --events for the raw journal
python3 -m polymath resume <session>     # continue after a crash or provider outage
python3 -m polymath workflow fix-until-green -w ./repo --input cmd="python3 -m unittest"
```

Python API:
```python
from polymath import Runtime, load_config
res = Runtime(load_config()).run("Fix the failing tests", "./repo", verify_command="python3 -m unittest")
print(res.state, res.stop_reason, res.answer)
```

## Verify it yourself

```bash
python3 -m unittest discover -s tests -t .    # 122 offline tests (~12 s): chaos, kill -9, spec conformance
python3 -m evals.reference                     # proves all 34 eval verifiers: reject empty, accept reference
python3 -m evals.runner --tasks core --out /tmp/run   # live evaluation (needs an API key)
```

## Documentation

| # | Document | What it answers |
|---|---|---|
| 00 | [Vision & principles](docs/00-vision-and-principles.md) | What we're building and the rules for every trade-off |
| 01 | [Requirements](docs/01-requirements.md) | Capabilities, FR/NFR with traceability to code and tests |
| 02 | [System architecture](docs/02-system-architecture.md) | C4 views, components, runtime flows, deployment |
| 03 | [Execution model](docs/03-execution-model.md) | **How it executes any task**: the universal algorithm, stop conditions, error matrix |
| 04 | [Harness specification](docs/04-harness-specification.md) | Invariants, event catalogue, storage, resume/replay semantics, concurrency |
| 05 | [Context engineering](docs/05-context-engineering.md) | Clearing, compaction, cache-shape discipline, calibration |
| 06 | [Tool system](docs/06-tool-system.md) | Tool contract, registry, catalogue, code mode, MCP mapping |
| 07 | [Terminal subsystem](docs/07-terminal-subsystem.md) | **Should agents have terminals?** Yes — and how to make one robust |
| 08 | [Determinism](docs/08-determinism.md) | Where determinism lives; workflows vs agents; replay |
| 09 | [Memory architecture](docs/09-memory-architecture.md) | Working, episodic, semantic, procedural memory |
| 10 | [Multi-agent orchestration](docs/10-multi-agent-orchestration.md) | Delegation contract; A2A mapping |
| 11 | [Model gateway](docs/11-model-gateway.md) | Streaming, retries, fail-over, protocols, NIM model matrix |
| 12 | [Observability](docs/12-observability.md) | Events, OTel GenAI spans, console, debugging playbook |
| 13 | [Evaluation & testing](docs/13-evaluation-and-testing.md) | Test pyramid, eval design, verifier validation |
| 14 | [Configuration & operations](docs/14-configuration-and-operations.md) | Config reference, CLI, runbook |
| 15 | [Performance & cost](docs/15-performance-and-cost.md) | Where time and tokens go, measured |
| 16 | [Roadmap](docs/16-roadmap.md) | What's next (and what isn't done) |
| 17 | [Prompt specification](docs/17-prompt-specification.md) | Every prompt the models see, verbatim (generated from code) |
| 18 | [**v2 architecture**](docs/18-v2-architecture.md) | Prebuilt harness + measured swaps: layers, turn sequence, result lifecycle, config |
| R | [**Research notes**](docs/research/README.md) | SDK audit (R1), context literature (R2), terminal bake-off (R3), egress governor (R4), stack bake-off (R5) |
| — | [ADRs](docs/adr/README.md) · [Glossary](docs/glossary.md) · [Schemas](spec/schemas) · [**Results**](docs/evaluation/RESULTS.md) | |

## Repository layout

```text
polymath/            v1: the agent & harness (stdlib only)
  kernel/            agent loop, runtime, router, verifier, prompts, workflows
  context/           projection, clearing/compaction engine, token calibration
  gateway/           OpenAI-compatible streaming client, fail-over, protocols, record/replay
  tools/             terminal, files, search, planning, memory, skills, web, delegate
  memory/ observability/ skills/   BM25 store · tracer & console · 7 built-in playbooks
  v2/                v2: Coder composition + swaps (terminal, recall, ledger), CLI   [extra: v2]
  egress/            egress governor: OpenAI-compatible rate-governing proxy       [extra: egress]
tests/               155 deterministic tests (no network; v2/egress tests skip without the extras)
evals/               36 live tasks with hidden verifiers (28 core, 6 hard, 2 retention), runner,
                     and backends for deepagents, Pydantic AI Coder, OpenAI Agents SDK, v2 + ablations
research/            terminal bake-off harness, SDK probes, raw results
spec/schemas/        JSON Schemas for every contract (validated against real runs)
docs/                architecture, specifications, ADRs, research notes, results
```
