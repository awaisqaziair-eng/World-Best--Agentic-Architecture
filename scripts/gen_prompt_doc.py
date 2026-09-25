"""Regenerate docs/17-prompt-specification.md from the prompt strings in the code.

    python3 scripts/gen_prompt_doc.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from polymath.context.engine import SUMMARY_SYSTEM  # noqa: E402
from polymath.gateway.protocols import TEXT_PROTOCOL_INSTRUCTIONS  # noqa: E402
from polymath.kernel.agent import NUDGE_EMPTY, NUDGE_LENGTH, NUDGE_TEXT  # noqa: E402
from polymath.kernel.prompts import SUBAGENT_ADDENDUM, SYSTEM_PROMPT, build_task_message  # noqa: E402
from polymath.kernel.router import ROUTER_PROMPT  # noqa: E402
from polymath.kernel.verifier import JUDGE_SYSTEM  # noqa: E402
from polymath.tools.skills import discover_skills, skills_index  # noqa: E402
from polymath.types import TaskSpec  # noqa: E402

TEMPLATE = Path(__file__).with_name("prompt_doc_template.md").read_text()


def main() -> None:
    example = build_task_message(
        TaskSpec("Compute total revenue per region from sales.csv and write report.json.", "/work", acceptance_criteria=["report.json has one key per region"]),
        workspace=Path("/nonexistent-example"),
        hints=["Possibly relevant skills: data-analysis (load_skill if useful)."],
    )
    doc = TEMPLATE.format(
        system_prompt=SYSTEM_PROMPT.format(skills=skills_index(discover_skills([]))),
        subagent=SUBAGENT_ADDENDUM.strip(),
        example=example,
        nudge_text=NUDGE_TEXT,
        nudge_empty=NUDGE_EMPTY,
        nudge_length=NUDGE_LENGTH,
        summary=SUMMARY_SYSTEM,
        judge=JUDGE_SYSTEM,
        router=ROUTER_PROMPT.strip(),
        textproto=TEXT_PROTOCOL_INSTRUCTIONS.strip(),
    )
    out = Path(__file__).resolve().parent.parent / "docs" / "17-prompt-specification.md"
    out.write_text(doc)
    print(f"wrote {out} ({len(doc):,} chars)")


if __name__ == "__main__":
    main()
