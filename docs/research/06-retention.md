# R6 — Retention under pressure: what actually decides whether an agent keeps its facts

> Status: **Measured (n = 2 per cell; mechanisms read from full transcripts)** · Date: 2026-09-25 · Tasks: [`evals/tasks/retention.py`](../../evals/tasks/retention.py) · Model: `nvidia/nemotron-3-ultra-550b-a55b` (NIM), temperature 0.3 · Window: `POLYMATH_V2_CONTEXT_WINDOW=16000` (a stress regime) · Transcripts: `runs/ultra-ret16k-*/transcripts/`

## 1. Question

R2 proposed four mechanisms: addressable eviction (H1), a machine-derived state ledger (H2), fault-driven pinning (H3) and reacquisition metrics (H5). Before any of them becomes a default: **do they change outcomes or costs, and why?**

## 2. Design

Four arms. The terminal is Polymath's in all of them, so only context handling differs. Each arm runs 2 tasks × 2 repeats.

| Arm | Old tool results | Ledger |
|---|---|---|
| `polymath-v2-terminal` | **Coder's prebuilt `ClearToolResults`** (70 % of the window, keeps the last 3 pairs, no size floor) | – |
| `polymath-v2-clear` | Polymath's `RecallableEviction` policy (evict at 50 % down to 30 %, keep 4 pairs, **never results < 250 tokens**), with the **irreversible** placeholder | – |
| `polymath-v2-recall` | the same policy, **addressable** stubs (handle + preview; `read_tool_result` recovers exactly) | – |
| `polymath-v2` | addressable | ✓ |

Tasks (verifiers self-tested both ways, 36/36):
- **`ret-token-audit`**: an audit token issued once (re-running the tool issues a *different* one), then six ~9 k-char metrics reports, then a report needing the token and each service's p99.
- **`ret-rename-changes`**: a rename across 12 large modules (one reference only inside a `getattr` string), then a report of exactly which files changed, checked against the real diff.

## 3. Results

| Arm | Task | Pass | Turns | Tokens (k) | Evictions | Recalls | Identical re-reads | Ledger injections |
|---|---|---|---|---|---|---|---|---|
| Coder clearing | token-audit | **0/2** | 11/11 | 171/171 | – | – | – | – |
| Coder clearing | rename | 2/2 | 23/31 | 180/255 | – | – | – | – |
| v2 policy, irreversible | token-audit | 2/2 | 11/11 | 215/215 | 5/5 | 0/0 | 0/0 | – |
| v2 policy, irreversible | rename | 2/2 | 14/18 | 223/239 | 13/13 | 0/0 | **7/7** | – |
| v2 policy, addressable | token-audit | 2/2 | 11/11 | 217/217 | 5/5 | 0/0 | 0/0 | – |
| v2 policy, addressable | rename | 2/2 | 19/19 | 316/347 | 7/12 | **7/7** | 0/0 | – |
| full v2 (addressable + ledger) | token-audit | 2/2 | **18/18** | **520/520** | 7/7 | **7/7** | 0/0 | 13/13 |
| full v2 (addressable + ledger) | rename | 2/2 | 16/26 | **119/195** | 7/7 | 0/0 | 0/0 | 11/15 |

## 4. Mechanisms (from the transcripts, not inferred from the numbers)

### 4.1 The prebuilt tier cleared an 11-token plan, and the model hallucinated (2 of 2)
In both failing runs the model read `services.txt` (44 characters: six service names) and ran the first three services correctly. Then `ClearToolResults` cleared everything but the last three pairs, **including that tiny result**, and the model continued with **the same three invented names** in both repeats (`notifications`, `orders`, `payments`; the real ones are `checkout`, `search`, `notify`). It never re-read the file. Polymath's policy never evicts results under 250 tokens, because clearing them saves almost nothing, so in all 12 v2-policy runs the list stayed in context.

**Finding F1.** *Which* results are evicted matters more than whether an evicted result can be recovered. A size floor is a one-line fix with a large effect, and the prebuilt tier doesn't have one.

*Caveat.* `metrics_report.py` accepts any service name, so the hallucination failed silently. A real tool that rejects unknown names would give the model a chance to notice. The task makes this failure as quiet as it can be.

### 4.2 The model keeps its own notes, in reasoning that eviction never touches
In the irreversible arm, the token's tool output was cleared two turns after it arrived. Yet the report carried the right token, because the model's reasoning right after the output said *"The token is `AT-BE33DB177FC1`"*. Pydantic AI sends reasoning back by default (`openai_chat_send_back_thinking_parts='auto'`), and no tier evicts reasoning. The model also noted each p99 in its reasoning as it went.

**Finding F2.** With reasoning carried back, this model is its own ledger for facts it *noticed*. Addressability therefore changed **no** pass rate here (the irreversible and addressable arms are 4/4 each). R2's H1 prediction ("fewer lost-fact failures") is **not supported on this model and harness**. What H1 does buy is shown next.

### 4.3 When the model didn't take notes, recall saved the run, at a price
In the full arm the model stopped writing p99 values into its reasoning partway through (msg 11 onward). When it wrote the report, those reports had been evicted. It **recalled all seven by handle** (`read_tool_result`) and produced a fully correct report, in both repeats. In the Coder arm the same situation (values not noted, results gone) produced invented values.

The price: 520 k tokens, against 215 k for the irreversible arm. The model read each 166-line report in full and never used `pattern` or `from_end`. The value it needed (`p99_ms = …`) sat three lines from the end, while the stub's one-line tail preview showed `END`. The follow-up arm `polymath-v2-tail3` (3-line tail preview) tests the obvious fix; see §6.

### 4.4 Recall replaces re-reading one-for-one; it doesn't reduce it
On rename, the irreversible arm re-read 7 evicted files (identical `read_file` calls) and the addressable arm recalled 7 by handle instead. Pass rates are equal. Tokens are not lower with recall: a recall returns the stored text, the same size as a re-read.

**Finding F3.** When the source is unchanged, recall and re-reading are equivalent. Recall is strictly better only when **re-running would give a different answer** (non-idempotent tools, changed files). This is exactly `ret-token-audit`'s design, but there the model's own notes pre-empted it (4.2).

### 4.5 The ledger: cheaper when it answers the question, dearer when it invites recall
- **Rename:** the ledger listed every file changed this run (from workspace diffs), so the model wrote `CHANGES.md` **without re-reading or recalling anything** (0/0). This was the cheapest v2 run (119 k / 195 k).
- **Token-audit:** with the ledger, the model recalled all 7 evicted reports in both repeats; without it, 0 in both. The only difference between the two arms is the ledger. Its last line, *"Earlier tool results were evicted to handles; … read_tool_result recovers it exactly"*, plausibly **invites** the model to go back even when it doesn't need to. That is a hypothesis to test, not a finding.

**Finding F4.** The ledger pays for itself when the facts the task needs are ones it records (files, commands, tests). Its closing hint should say *when* to recall ("only if you need exact text you didn't note"), not merely *that* recall exists.

## 5. What this changes

| Decision | Before | After (evidence) |
|---|---|---|
| Size floor on eviction | a v2 detail (`min_result_tokens=250`) | **Keep; the headline difference from the prebuilt tier** (F1, 2/2 vs 0/2) |
| Addressable eviction (H1) | default on | **Keep, but reframe:** it doesn't raise pass rates when the model self-notes (F2); it is the safety net when the model *didn't* note something (4.3), and the only correct option for non-idempotent tools (F3) |
| Stub preview | 1 tail line | **3 tail lines, harness footers stripped** (F5: recalls 7 → 1, −55 % tokens) |
| Ledger (H2) | default on | keep; the recall-hint hypothesis (F4) was refuted by F5, so no rewording |
| R2 H1 prediction | "fewer lost-fact failures" | **not supported** on this model; failures came from *what* was evicted, not recoverability |

## 6. Follow-up: 3-line tail preview (`polymath-v2-tail3`, full v2 otherwise)

The first run of this arm was **invalid**. Every `bash` result ends with the terminal's footer (`[exit code: 0 | 0.02s | cwd: /long/path]`), so the "last 3 lines" were the note, `END` and the footer, whose long path used up the preview budget, and the p99 line never appeared (7 recalls again, 528 k). Two fixes followed: stubs now drop harness footers before taking the tail (keeping a compact `exit N`), and each tail line is truncated on its own, so a long earlier line can't push out the final one (a unit test caught that second flaw). The invalid run is kept aside, labelled.

Valid run:

| Arm | Task | Pass | Turns | Tokens (k) | Evictions | Recalls |
|---|---|---|---|---|---|---|
| full v2, 1-line tail | token-audit | 2/2 | 18/18 | 520/520 | 7/7 | **7/7** |
| full v2, **3-line tail** | token-audit | 2/2 | 12/11 | **240/225** | 6/5 | **1/1** |
| full v2, 3-line tail | rename | 1/1 (r2 pending) | 17 | 134 | 12 | 0 |

**Finding F5.** A stub that shows the lines where results keep their conclusions (the tail) cut recalls from 7 to 1 and tokens by ~55 %, with every pass kept. It also **refutes the F4 hypothesis** that the ledger's recall hint drove the recall storm. The ledger was present in both rows; what changed was whether the stub carried the needed line. **Default changed to 3 lines.**

## 7. Threats to validity

- **n = 2 per cell, one model.** The failure mechanisms (4.1, 4.3) are read directly from transcripts and identical across repeats, so they are firmer than the counts. Cost differences under ~30 % are within run-to-run variation (e.g. rename turns 23 vs 31 in one arm).
- **Reasoning carryover is a property of this harness–model pair.** A harness that drops reasoning (Polymath v1's default `send_reasoning_back=False`), or a model that doesn't restate facts, would put far more weight on H1. F2 isn't a general claim.
- **Stress regime.** 16 k is small for modern models. It was chosen to force eviction within short tasks. Real long-horizon runs reach the same pressure over hours rather than minutes.
- **Author-written tasks.** Designed to make loss detectable, so they over-represent exactly the situations the mechanisms target.
