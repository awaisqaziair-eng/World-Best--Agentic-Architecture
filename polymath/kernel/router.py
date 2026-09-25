"""Task intake: classify a task into a profile that tunes the run.

The router never changes *what* the agent does — it adjusts budgets and adds
hints (which skills are relevant, whether the work is parallelisable). The
default heuristic router is free, instant and deterministic; the optional LLM
router asks the utility model for the same profile as JSON.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .. import jsonutil
from ..gateway.base import ChatRequest, ModelClient
from ..types import Message

CATEGORIES = ("coding", "debugging", "data", "research", "writing", "ops", "math", "git", "web", "general")

# Word-boundary regexes (identifiers like `merge_intervals` must NOT match `merge`:
# `_` is a word character, so \bmerge\b does not fire inside it — regression-tested).
_KEYWORDS: dict[str, tuple[str, ...]] = {
    "debugging": (r"bugs?", r"fix(es|ed)?", r"failing", r"fails?", r"errors?", r"exceptions?", r"traceback", r"crash(es|ed)?", r"broken", r"debug"),
    "coding": (r"implement\w*", r"functions?", r"class(es)?", r"refactor\w*", r"modules?", r"scripts?", r"cli", r"unittest", r"unit tests?", r"optimi[sz]e\w*", r"rename"),
    "data": (r"csv", r"json", r"datasets?", r"sqlite", r"sql", r"aggregat\w*", r"revenue", r"average", r"statistics", r"columns?", r"rows?"),
    "research": (r"research", r"find out", r"investigate", r"documents", r"sources", r"knowledge base", r"which service", r"who (currently )?leads?", r"corpus"),
    "writing": (r"write an? (summary|report|readme|essay|document\w*)", r"readme", r"summary", r"report", r"documentation", r"essay", r"translate", r"\d+\s*[–-]\s*\d+ words"),
    "ops": (r"server", r"deploy\w*", r"archive", r"tar(\.gz)?", r"permissions", r"cron", r"processes", r"disk", r"organi[sz]e", r"sub-?folders?", r"checksum", r"log", r"access\.log"),
    "math": (r"how many", r"compute", r"calculate", r"probability", r"integers?", r"primes?", r"digit sum", r"paths"),
    "git": (r"git", r"branch(es)?", r"commits?", r"rebase", r"tag(ged)?"),
    "web": (r"https?://\S+", r"urls?", r"endpoints?", r"websites?", r"fetch", r"download", r"port \d+"),
}
_COMPILED = {c: [re.compile(rf"\b(?:{p})\b") for p in pats] for c, pats in _KEYWORDS.items()}

_SKILLS_FOR = {
    "coding": ["python-engineering"],
    "debugging": ["debugging", "python-engineering"],
    "data": ["data-analysis"],
    "research": ["research-synthesis"],
    "writing": ["technical-writing"],
    "git": ["git-workflow"],
    "web": ["web-services"],
    "ops": [],
    "math": [],
    "general": [],
}


@dataclass
class TaskProfile:
    category: str = "general"
    secondary: list[str] = field(default_factory=list)
    complexity: str = "medium"  # low | medium | high
    parallelizable: bool = False
    skills: list[str] = field(default_factory=list)
    source: str = "heuristic"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def budget_multiplier(self) -> float:
        return {"low": 0.5, "medium": 1.0, "high": 1.5}.get(self.complexity, 1.0)

    def hints(self, available_skills: set[str]) -> list[str]:
        out: list[str] = []
        sk = [s for s in self.skills if s in available_skills]
        if sk:
            out.append("Possibly relevant skills: " + ", ".join(sk) + " (load_skill if useful).")
        if self.complexity == "high":
            out.append("This looks like a multi-step task: start by writing a plan with `todo`.")
        if self.parallelizable:
            out.append("Parts of this task look independent; consider `delegate` to run them in parallel.")
        return out


def heuristic_profile(instruction: str) -> TaskProfile:
    text = instruction.lower()
    scores = {c: sum(len(rx.findall(text)) for rx in rxs) for c, rxs in _COMPILED.items()}
    if not re.search(r"\bgit\b", text) and scores["git"] < 2:
        scores["git"] = 0  # "branch"/"tag" alone are too ambiguous to imply git work
    # Stable ranking: score desc, then category order as declared (not alphabetical noise).
    order = {c: i for i, c in enumerate(_KEYWORDS)}
    ranked = sorted(((s, c) for c, s in scores.items() if s > 0), key=lambda sc: (-sc[0], order[sc[1]]))
    category = ranked[0][1] if ranked else "general"
    secondary = [c for s, c in ranked[1:3] if s >= 2]  # secondary skills need real evidence
    n_req = len(re.findall(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+", instruction)) + instruction.count(";")
    words = len(instruction.split())
    complexity = "low" if words < 40 and n_req <= 1 else "high" if (words > 220 or n_req >= 6) else "medium"
    parallel = bool(re.search(r"\b(each of|for each|all of the following|independent|several (topics|files|questions))\b", text))
    # Measured (docs/evaluation/RESULTS.md §Router): keyword routing is right ~75% of the time, and a
    # wrong skill hint actively misleads the model. So skills are only suggested on a clear winner.
    confident = bool(ranked) and ranked[0][0] >= 2 and (len(ranked) == 1 or ranked[0][0] > ranked[1][0])
    skills: list[str] = []
    for c in ([category, *secondary] if confident else []):
        for s in _SKILLS_FOR.get(c, []):
            if s not in skills:
                skills.append(s)
    return TaskProfile(category, secondary, complexity, parallel, skills[:3], "heuristic")


ROUTER_PROMPT = """Classify the task for an autonomous agent. Reply with ONLY a JSON object:
{"category": one of %s, "secondary": [up to 2 more categories], "complexity": "low"|"medium"|"high",
 "parallelizable": true|false}
Task:
""" % (list(CATEGORIES),)


def llm_profile(instruction: str, client: ModelClient) -> TaskProfile:
    base = heuristic_profile(instruction)
    try:
        # Reasoning models spend output tokens thinking before the JSON; 300 tokens (v1) starved them.
        r = client.complete(ChatRequest(messages=[Message("user", ROUTER_PROMPT + instruction[:6000])], max_tokens=2000, temperature=0.0, purpose="router"))
        d = jsonutil.loads_lenient(r.content or "", expect=dict)
    except Exception:
        return base
    cat = d.get("category") if d.get("category") in CATEGORIES else base.category
    sec = [c for c in d.get("secondary") or [] if c in CATEGORIES and c != cat][:2]
    cx = d.get("complexity") if d.get("complexity") in ("low", "medium", "high") else base.complexity
    skills: list[str] = []
    for c in [cat, *sec]:
        for s in _SKILLS_FOR.get(c, []):
            if s not in skills:
                skills.append(s)
    return TaskProfile(cat, sec, cx, bool(d.get("parallelizable", base.parallelizable)), skills[:3], "llm")
