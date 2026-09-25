# R2 — Context management: what the literature establishes, and what it leaves open

> Status: **Literature review + design hypotheses** · Date: 2026-09-25
>
> Reading rule: every number below is **as reported by the authors** (from the arXiv abstract pages, read 2026-09-24/25). None has been reproduced here. The hypotheses in §4 are ours and are tested in the retention benchmark (task #12). Until then they are claims, not results.

## 1. The problem in one paragraph

A long-running agent accumulates tool outputs, file contents and reasoning faster than any context window can hold them. Every harness must therefore decide, turn after turn, **what stays in the window, what leaves, in what form, and how it can come back**. Doing this badly costs in three ways:

- **Correctness**: the model forgets a fact it needs, or acts on a stale one.
- **Money and latency**: tokens re-sent every turn, and prompt-cache invalidation.
- **Hidden work**: the model re-reads or re-runs things it already had (reacquisition).

Most shipped harnesses optimise the second cost and measure only task completion, which hides the third (§2.4).

## 2. Sources

### 2.1 Addressable Recall Compaction (ARC): arXiv [2607.25066](https://arxiv.org/abs/2607.25066)
- **Mechanism.** Tool observations go into an append-only, **ID-addressable log**. On compaction, old observations are replaced by *compact citations*, and the agent can request any cited observation back by ID, with no re-execution and no similarity search. Archival storage is separated from what is presented in the active context.
- **Reported.** Needle-in-a-Haystack exact-answer accuracy **99.40 % vs 88.12 %** for the best baseline; LongBench-v2 Hard **29.97 % vs 28.25 %** (Qwen3-8B at 16 k, Qwen3-32B at 32 k); lower serving time and HBM traffic under their hardware model.
- **Our reading.** The large gain is on retrieval of a specific planted fact, exactly the case that summaries destroy. The small gain on LongBench-v2 Hard says addressability is no substitute for reasoning. The paper's own caution ("may remove task-critical details") applies: an evicted item is only as recoverable as the model's decision to ask for it.

### 2.2 The Missing Memory Hierarchy: demand paging (Pichay): arXiv [2603.09023](https://arxiv.org/abs/2603.09023)
- **Mechanism.** Treat the context window as **L1 cache**, not main memory. A transparent proxy evicts stale content (old tool results, definitions), detects **page faults** (the model re-requests evicted material) and **pins** pages whose fault history shows they belong to the working set.
- **Reported.** **0.0254 %** fault rate across 1.4 M simulated evictions; production context use cut **up to 93 %** (5,038 KB → 339 KB); 681 turns. **21.8 %** of context across 857 production sessions was "structural waste".
- **Acknowledged limits.** Under sustained extreme pressure the system shows **thrashing** (repeated fault-in of evicted content). Cross-session persistence is unimplemented.
- **Our reading.** Pichay is the first to treat reacquisition as a *signal to act on* (pinning), not just a cost. The thrashing admission matters: any eviction policy needs a feedback loop.

### 2.3 TokenPilot: arXiv [2606.17016](https://arxiv.org/abs/2606.17016)
- **Mechanism.** Two granularities.
  - *Ingestion-aware compaction* stabilises the prompt prefix and removes environmental noise **at the moment content enters** the context.
  - *Lifecycle-aware eviction* tracks each segment's **residual utility** and offloads it on a conservative **batch-turn schedule** only once its task relevance has expired.
- **Key observation.** Naive pruning mutates the sequence, breaks prefix matches and **invalidates the prompt cache**, so a "cheaper" context can cost more.
- **Reported.** 61 % / 56 % cost reduction (isolated mode); 61 % / 87 % (continuous mode) on PinchBench and Claw-Eval, with competitive task performance.
- **Our reading.** This is the cache-economics argument made rigorous: evictions should be *rare and batched*, not continuous.

### 2.4 What does context compression cost an agent? arXiv [2608.16370](https://arxiv.org/abs/2608.16370)
- **Finding.** Compression has an **interaction cost that completion metrics hide**: *reacquisition*. At 5× compression (GPT-5.5), completion stayed flat (80 % → 85 %, p = 1.0) while **retrieval calls rose from 21.0 to 63.9** (p = .002). Retrieval rose in all six model-regime comparisons. Replacing retained content with semantically *irrelevant* content raised retrieval by **57 %** without changing completion, so **validity of what is retained matters more than quantity**. The effect appeared in a planning environment but not in ALFWorld, so it depends on the environment.
- **Recommendation.** Report tool-call decomposition (retrieval vs execution), state retention and validity, and use controlled interventions.
- **Our reading.** This paper changes *how we evaluate*: a compaction policy that "doesn't hurt pass rate" can still triple the work. Our benchmark must count reacquisition.

### 2.5 Context Compaction Theory: arXiv [2608.01326](https://arxiv.org/abs/2608.01326)
- **Model.** Two games.
  - *Context Selection*: keep a subset of the state.
  - *Context Generation*: write an arbitrary bounded message (a summary).
- **Result.** The generation game is **exactly one-way communication complexity**. Selection is a restricted protocol, and for some query sets **generation needs strictly less budget than selection**. The authors also benchmark Anthropic's compaction endpoint on set-membership queries against optimal strategies.
- **Our reading.** Neither pure strategy dominates. Selection is *verifiable*: what is kept is verbatim, so it cannot be a hallucinated state. Generation is *more compact* but lossy in unknowable ways. The design consequence is a **hybrid**: select the facts that must be exact, and generate the narrative.

### 2.6 Context as an Environment (Scroll): arXiv [2608.21690](https://arxiv.org/abs/2608.21690)
- **Mechanism.** The session is an append-only **event log** plus a **persistent Python kernel**. Tool outputs, retrieved history and derived state are bound to *variables* instead of being serialised into the prompt. The model writes code to search, materialise and transform session state, and only what it explicitly prints enters its view. Stale spans are evicted but remain **recoverable** through an index of exact event-log addresses.
- **Reported (Qwen 3.8-Max).** LongMemEval_S **94.8 %**; BEAM_10M **73.1 %** (+5.1 over prior best); LOCA_256K **86.7 %** (+37.4 over the prior best long-horizon agent).
- **Our reading.** The strongest evidence so far that *lossless log + addressable recall + programmatic access* beats fixed compression at long horizons. Polymath already has two of the three ingredients: the event-sourced session log and a persistent terminal in which the model can already keep state in files and variables.

### 2.7 Also read
- **ACON**, arXiv [2510.00615](https://arxiv.org/abs/2510.00615): optimises the *compression guidelines* themselves from failure analysis. This supports treating the summary prompt as a tunable artefact.
- **CompactionRL**, arXiv [2607.05378](https://arxiv.org/abs/2607.05378): trains the policy to operate across compaction boundaries. Model-side, out of scope for a harness.
- **Learning Agent-Compatible Context Management**, arXiv [2605.30785](https://arxiv.org/abs/2605.30785), and **ACE**, arXiv [2606.31564](https://arxiv.org/abs/2606.31564): learned or pluggable context policies. Noted, not used.
- **The Harness Effect**, arXiv [2607.06906](https://arxiv.org/abs/2607.06906): switching orchestration (not model) cut cost **41 %**, latency **44 %** and tokens **38 %** at quality parity across six models. Its representative mechanisms include "cache-shape discipline" and "failure-spend governance". This is the evidence that harness engineering is a first-order lever.
- **Anthropic engineering, "Effective context engineering for AI agents"**: context rot, attention budget, just-in-time retrieval by lightweight identifiers, compaction, structured note-taking.
- **Anthropic engineering, "Managed Agents"**: session (append-only log) / harness (stateless loop) / sandbox decoupling, with recovery by replaying the session. This is the same architecture as Polymath's event-sourced kernel (ADR-002).

## 3. What the prebuilt SDKs already implement (from R1)

| Idea | Best prebuilt implementation | Gap |
|---|---|---|
| Ingestion-time reduction with handles (TokenPilot ingestion + ARC addressability) | Pydantic AI `ToolOutputLimits` `Spill`: handle + preview + bounded `read_tool_result` | Size-gated only: a result that is *moderate when produced* but *stale later* is never given a handle |
| Clearing old results | `ClearToolResults`, `ClearToolUsesEdit` | **Irreversible** (harness README: reading the run back "does not recover what the receipt says was dropped") |
| Summarisation | `SummarizingCompaction` (incremental, receipts, pin) | Pure *generation*: no guaranteed-exact state |
| Recall over history | `ConversationSearch` (BM25) | Similarity search, not exact addressing (ARC's point) |
| Pinning | `pin()` | Manual; no fault feedback (Pichay) |
| Cache economics | `min_clear_tokens` | A threshold, not a cost model; not batch-scheduled (TokenPilot) |
| Measuring reacquisition | none | Every SDK reports tokens; none reports re-fetches |

## 4. Design hypotheses for Polymath v2 (to be tested, not asserted)

Each hypothesis fills one gap in §3, is implementable as a `CompactionStrategy` tier inside Pydantic AI's `TieredCompaction` (or as LangChain middleware), and states a falsifiable prediction.

**H1: Addressable eviction.** When an old tool result is evicted, replace it with a stub carrying a stable handle into the session's append-only event log (`⟨evicted r:17 · read_file src/app.py · 212 lines · recall("r:17")⟩`), plus a bounded `recall(handle, offset, limit, pattern)` tool. This is ARC applied at *eviction* time, which Spill does not cover. *Prediction:* same pass rate as irreversible clearing, with fewer **re-executions** (re-running a command or re-reading a file that changed) and a correct answer where re-execution would give a different result.

**H2: Deterministic state ledger (selection for facts, generation for narrative).** A ledger derived **mechanically from the event log**, never by an LLM, survives every compaction verbatim:
- files written (path, hash, last-writer turn);
- commands run (exit codes);
- latest test and verification results;
- todo state;
- decisions the model has declared.

The generated summary carries only the narrative. *Prediction:* fewer post-compaction errors of the "acted on a stale fact" kind; ledger contents are verifiably correct by construction (Context Compaction Theory: selection cannot hallucinate).

**H3: Fault-driven pinning with thrash control.** Every `recall` of an evicted handle, and every re-execution of an identical read, counts as a **page fault** against that item. A faulted item is pinned for *k* turns. A fault rate above a threshold triggers a larger working set, and the event is logged as thrashing. *Prediction:* lower reacquisition than LRU-by-age eviction at the same window budget.

**H4: Batched, cache-economic eviction.** Evict only in batches at turn boundaries, only when the expected saving over the remaining turns exceeds the cost of re-writing the cache suffix after the edit point:

`Δtokens × E[remaining requests] > suffix_tokens_after_edit × cache_write_premium`

between high and low watermarks (hysteresis), so eviction is rare. *Prediction:* the same peak-context bound with fewer cache-invalidating edits than threshold clearing.

**H5: Measure what the papers say is hidden.** The benchmark reports, per policy:
- pass rate;
- total and peak tokens;
- **reacquisition calls** (recalls + identical re-reads + identical re-executions);
- **cache-invalidating edits**;
- **ledger-verified state errors**.

This hypothesis is a commitment about evaluation, not about mechanism. Without it, H1–H4 can't be judged.

## 5. What would falsify the approach

- If irreversible clearing plus the model's own re-reads show **no measurable reacquisition or correctness gap**, H1 and H3 add complexity for nothing. On our core tasks that is plausible, because most are short. The benchmark must therefore include long-horizon tasks built to need an early fact late.
- If the ledger never catches an error that the summary alone would have caused, H2 is overhead.
- If NIM-hosted models don't benefit from prompt caching in cost or latency, H4's cost model reduces to "evict rarely", which v1 already does.
