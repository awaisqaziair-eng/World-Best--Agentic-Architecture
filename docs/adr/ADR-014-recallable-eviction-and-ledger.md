# ADR-014 — Make evicted context addressable, and keep a machine-derived state ledger

**Status:** Accepted (evidence below; refinements in progress) · **Date:** 2026-09-25 · Research: [R2](../research/02-context-management-literature.md) · Code: `polymath/v2/recall.py`, `polymath/v2/ledger.py`

## Context
- **The prebuilt eviction is irreversible.** The best prebuilt context toolkit (Pydantic AI harness) replaces old tool results with `[tool result cleared]`, and its own README says the persisted run "does not recover what the receipt says was dropped". `Spill` is lossless, but only for results that were large *when produced*.
- **What the literature says about that** (R2):
  - addressable recall preserves facts that summaries destroy (ARC: 99.4 % vs 88.1 % needle accuracy);
  - reacquisition is a hidden cost that completion rates don't show (21 → 64 retrieval calls at flat completion);
  - selection is verifiable, while generation is not (Context Compaction Theory).
- **Re-running a tool is not a recovery strategy** when the world has changed since the first run.

## Decision
Two capabilities that plug into the prebuilt harness rather than replacing it.

1. **`RecallableEviction`** replaces `ClearToolResults`.
   - **Batching.** Oldest-first eviction to a low watermark, as one rewrite per episode (the same batching as the prebuilt tier).
   - **Addressable stubs.** Every evicted result goes into the **same `OverflowStore` that `ToolOutputLimits` spills to**. It is replaced by a stub naming the tool, its arguments, size, first and last line, and the handle. The model recovers it exactly with the existing `read_tool_result`, so no tool is added.
   - **Fault-driven pinning.** Page faults (recalls, identical re-reads) pin the re-fetched copy; faults on a "hot" item pin twice as long. Thrashing, i.e. too many faults in a window, raises the watermark.
   - **Measurement.** Reacquisition counters are recorded for every run.
   - **Control arm.** `addressable=False` keeps the identical policy with the prebuilt irreversible placeholder, which isolates the effect of addressability.
2. **`StateLedger`**:
   - Files changed (by workspace snapshot diff, so shell writes count), commands, exit codes and latest test status, all derived from tool executions, never by an LLM.
   - Appended ephemerally to the request tail (the same cache-safe placement as `SystemReminders`), **only once context has become lossy**. It is never persisted.

## Alternatives considered
- **Prebuilt `ClearToolResults` as-is.** Cheapest, but irreversible (the gap above).
- **`ToolOutputLimits(Spill)` with a low threshold** (spill everything > ~1 k chars). Lossless, but it pays the stub cost on every result from the first turn, even in short tasks where nothing ever needs evicting.
- **`ConversationSearch` (BM25).** Recall by similarity, not by address. ARC's result is that exact addressing is what fixes needle-type loss.
- **Summarisation only.** Generation can't guarantee exact state (Context Compaction Theory), which is why the ledger exists alongside it.
- **Cache-economic eviction (H4).** Downgraded: the prebuilt tier is already batched, and only a cost model would be new.

## Consequences
- \+ The loss is recoverable and measured: a run reports how often the model had to go back for something.
- \+ Composable: all four behaviours are harness capabilities, and every flag can be turned off.
- − More moving parts in the context path. Each has offline tests (11, driving a real agent with scripted models).
- − Each stub costs **≈ 75–90 tokens** with the default 3-line tail (measured on typical `read_file`, report and `pytest` stubs; ≈ 65–75 with a 1-line tail). A first draft measured 112 tokens, mostly a long handle printed twice; handles are now `ev/<run-id tail>/<n>`. Results under `min_result_tokens` (250) are never evicted, because it wouldn't pay.

## Evidence ([R6](../research/06-retention.md), n = 2 per cell, nemotron-3-ultra, 16 k window)

- **The eviction *policy* made the difference; recoverability did not change a pass rate here.** Coder's prebuilt clearing failed `ret-token-audit` **0/2**: it cleared an 11-token service list, and the model invented three services, the same three in both repeats. Every Polymath-policy arm (irreversible, addressable, addressable + ledger) passed **4/4**. The reason is the size floor: results under 250 tokens are never evicted.
- **Why addressability didn't matter for pass rates:** Pydantic AI sends reasoning back, and the model had noted the facts it needed in its reasoning, which no tier evicts. When the model *hadn't* noted values, recall by handle rescued the run (both repeats), at 2.4× the tokens.
- **The ledger** made the rename task the cheapest v2 run (its file list answered the task's question with no re-reading). On the token task it coincided with 7 recalls per run instead of 0, plausibly invited by its closing hint.

**Status after the evidence:** accepted. The size floor is the load-bearing part. Addressable eviction stays on as the safety net for un-noted facts and non-idempotent tools. Stub previews (3-line tail) and the ledger's recall hint are being refined against R6's transcripts.
