# 17 — Prompt Specification

> Status: **Generated from code** by `scripts/gen_prompt_doc.py`. Prompts are part of the architecture: every string the model sees is listed here verbatim.

## 1. Prompt inventory

| Prompt | Where | Sent to | When |
|---|---|---|---|
| System prompt | `kernel/prompts.py::SYSTEM_PROMPT` | agent model | every turn (stable prefix) |
| Sub-agent addendum | `kernel/prompts.py::SUBAGENT_ADDENDUM` | agent model | every turn of a sub-agent |
| Task message | `kernel/prompts.py::build_task_message` | agent model | first user message, pinned |
| Harness nudges | `kernel/agent.py::NUDGE_*`, verifier/budget/loop messages | agent model | on specific conditions |
| Compaction summariser | `context/engine.py::SUMMARY_SYSTEM` | utility model | when compacting |
| Verification judge | `kernel/verifier.py::JUDGE_SYSTEM` | utility model | on `finish` with acceptance criteria |
| LLM router | `kernel/router.py::ROUTER_PROMPT` | utility model | intake, if `router = llm` |
| Text tool protocol | `gateway/protocols.py::TEXT_PROTOCOL_INSTRUCTIONS` | agent model | appended to the system prompt in text-protocol mode |
| Workflow prompts | `kernel/workflows_builtin.py` | primary model / agents | inside workflows |

## 2. Design rules

1. **Right altitude** — heuristics and principles, not brittle if-then scripts; specific enough to shape behaviour (Anthropic, *Effective context engineering*, 2025).
2. **Stable prefix** — the system prompt contains no dates, paths or task data (invariant I7); task data goes in the task message.
3. **Method over persona** — the prompt encodes the operating loop (Understand → Plan → Act → Verify → Deliver) and the failure-handling habits, not a character.
4. **Tools describe themselves** — tool-specific guidance lives in tool descriptions ([06](06-tool-system.md)), not in the system prompt.
5. **Harness messages are prefixed** `[harness]` / `[verifier]` so the model can distinguish them from the user.

## 3. System prompt (verbatim, with the built-in skills index)

```text
{system_prompt}
```

## 4. Sub-agent addendum

```text
{subagent}
```

## 5. Task message (example rendering)

```text
{example}
```
The environment snapshot, memories, hints and an auto-loaded playbook (when routing is confident) are added by the kernel; the example above has no playbook and a non-existent workspace.

## 6. Harness messages

| Condition | Message |
|---|---|
| Prose reply that announces intent (not final) | `{nudge_text}` |
| Empty reply | `{nudge_empty}` |
| Output cut at max length | `{nudge_length}` |
| Identical call repeated ≥ 3× | `[harness] You have made the identical `<tool>` call N times and got the same result. Repeating it will not change the outcome. Step back, re-read the output, question your assumptions, and try a different approach.` |
| 4 consecutive tool errors | `[harness] Your last 4 tool calls failed. Pause and diagnose: re-read the error messages, check your assumptions (paths, working directory, argument formats, syntax), and simplify the next step.` |
| 80 % of any budget | `[harness] You have used N% of your budget (…). Prioritise: finish the essential parts, verify them, and call `finish` soon.` |
| Budget exhausted | `[harness] <reason>. Stop working now and call `finish` with your best final answer: what is complete, the current state, and what remains undone.` |
| Verification failed | `[verifier] Your submission did not pass verification (attempt i/N).` + details + `Fix the problems, re-check your work, then call `finish` again.` |

## 7. Compaction summariser (system)

```text
{summary}
```

## 8. Verification judge (system)

```text
{judge}
```

## 9. LLM router

```text
{router}
<task instruction>
```

## 10. Text tool protocol (appended in text mode)

```text
{textproto}
## <tool name>
<description>
Parameters (JSON Schema): <schema>
```
