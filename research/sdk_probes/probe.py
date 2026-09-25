"""Hands-on SDK probes against NVIDIA NIM (reproducible evidence for ADR-011).

Each probe gives the SAME micro-task to an off-the-shelf agent built with a
prebuilt SDK, and checks the result on disk (not the model's claim):

    task: write fib.py printing the 30th Fibonacci number (F(30)=832040 with F(1)=F(2)=1),
          run it, and write only that number to answer.txt

usage: python research/sdk_probes/probe.py <stack> <model>
stacks: langchain | deepagents | pydanticai
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

NIM = "https://integrate.api.nvidia.com/v1"
KEY = os.environ["NVIDIA_NIM_API_KEY"]
TASK = ("In the current working directory, write fib.py that prints the 30th Fibonacci number "
        "(with F(1)=F(2)=1), run it, and write only that number to answer.txt. Then reply DONE.")


def shell_tool_fn(ws: Path):
    def run_shell(command: str) -> str:
        """Run a bash command in the workspace and return combined stdout/stderr and the exit code."""
        p = subprocess.run(["bash", "-c", command], cwd=ws, capture_output=True, text=True, timeout=120)
        return (p.stdout + p.stderr)[-8000:] + f"\n[exit code {p.returncode}]"
    return run_shell


def write_file_fn(ws: Path):
    def write_file(path: str, content: str) -> str:
        """Create or overwrite a file (path relative to the workspace)."""
        f = ws / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
        return f"wrote {len(content)} chars to {path}"
    return write_file


def probe_langchain(model: str, ws: Path) -> dict:
    from langchain.agents import create_agent
    from langchain_openai import ChatOpenAI
    llm = ChatOpenAI(model=model, base_url=NIM, api_key=KEY, temperature=0.3, max_retries=6, timeout=240, streaming=True)
    agent = create_agent(llm, tools=[shell_tool_fn(ws), write_file_fn(ws)], system_prompt="You are a coding agent. Use the tools.")
    out = agent.invoke({"messages": [{"role": "user", "content": TASK}]}, {"recursion_limit": 40})
    msgs = out["messages"]
    return {"model_calls": sum(1 for m in msgs if m.type == "ai"), "final": str(msgs[-1].content)[:200]}


def probe_deepagents(model: str, ws: Path) -> dict:
    from deepagents import create_deep_agent
    from deepagents.backends import LocalShellBackend
    from langchain_openai import ChatOpenAI
    llm = ChatOpenAI(model=model, base_url=NIM, api_key=KEY, temperature=0.3, max_retries=6, timeout=240, streaming=True)
    agent = create_deep_agent(model=llm, backend=LocalShellBackend(root_dir=str(ws), virtual_mode=False) if "virtual_mode" in LocalShellBackend.__init__.__code__.co_varnames else LocalShellBackend(root_dir=str(ws)))
    out = agent.invoke({"messages": [{"role": "user", "content": TASK + f" The working directory is {ws}."}]}, {"recursion_limit": 60})
    msgs = out["messages"]
    return {"model_calls": sum(1 for m in msgs if m.type == "ai"), "final": str(msgs[-1].content)[:200]}


def probe_pydanticai(model: str, ws: Path) -> dict:
    from pydantic_ai import Agent
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider
    from pydantic_ai_harness.coder import Coder
    m = OpenAIChatModel(model, provider=OpenAIProvider(base_url=NIM, api_key=KEY))
    agent = Agent(m, capabilities=[Coder(workspace=ws)])
    res = agent.run_sync(TASK)
    return {"model_calls": res.usage.requests, "final": str(res.output)[:200]}


if __name__ == "__main__":
    stack, model = sys.argv[1], sys.argv[2]
    ws = Path(tempfile.mkdtemp(prefix=f"probe_{stack}_"))
    os.chdir(ws)
    t0 = time.time()
    rec = {"stack": stack, "model": model}
    try:
        rec.update({"langchain": probe_langchain, "deepagents": probe_deepagents, "pydanticai": probe_pydanticai}[stack](model, ws))
        ans = (ws / "answer.txt").read_text().strip() if (ws / "answer.txt").exists() else None
        rec["passed"] = ans == "832040"
        rec["answer_file"] = ans
    except Exception as e:
        rec["passed"] = False
        rec["error"] = f"{type(e).__name__}: {str(e)[:500]}"
        rec["trace_tail"] = traceback.format_exc()[-1200:]
    rec["seconds"] = round(time.time() - t0, 1)
    print(json.dumps(rec))
