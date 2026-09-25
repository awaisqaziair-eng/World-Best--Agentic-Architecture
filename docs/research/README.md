# Research notes (v2)

These notes back the v2 decision to build on **prebuilt SDKs, enhanced only where they measurably fall short**. The rules they follow:

1. **Source over docs.** Behaviour is read from the installed code, with versions pinned and file:line given where it matters. Documentation-only claims are marked *(doc)*.
2. **Measure, then decide.** Every design choice cites a measurement or names the experiment that will test it.
3. **Audit your own benchmark first.** Each red cell is checked at the raw-output level before it is believed. Errors found in our own tooling are recorded, not quietly fixed (R3 §6 lists four).
4. **Report what went wrong.** Wrong predictions and corrected assumptions stay in the text.

| Note | Question | Status |
|---|---|---|
| [R1 — SDK landscape](01-sdk-landscape.md) | What do LangChain, deepagents, Pydantic AI + harness and the OpenAI Agents SDK actually do? Where do they fail? | Verified in source; 10 reproducible findings (D1–D10) |
| [R2 — Context-management literature](02-context-management-literature.md) | What does research establish about eviction, compaction and recall, and what do prebuilt SDKs leave open? | Review + falsifiable hypotheses H1–H5 |
| [R3 — Terminal bake-off](03-terminal-bakeoff.md) | Is Polymath's terminal still worth carrying into v2? | Measured: 14/14 vs best prebuilt 10/14; upstream bug D1 found |
| [R4 — Egress governor](04-egress-governor.md) | How do we stop account-level rate limits from deciding eval outcomes, for every SDK at once? | Implemented; 0 give-ups under 8-agent load |
| [R5 — Stack bake-off](05-stack-bakeoff.md) | Which prebuilt stack is the best foundation, on the same tasks, model and verifiers? | Running (4 stacks × 28 tasks; paired Coder vs v2 on a second model) |
| [R6 — Retention](06-retention.md) | Do addressable eviction and the ledger change outcomes or costs, and why? | Measured (n = 2): the size floor is what matters; H1's prediction not supported on this model |

Harness code for these experiments lives in [`research/`](../../research) and [`evals/backends.py`](../../evals/backends.py).
