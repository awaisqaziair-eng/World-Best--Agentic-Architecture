"""Stack-agnostic agent backends for the evaluation runner.

Every backend runs one task in a workspace and returns a ``BackendResult`` so the
SAME hidden verifiers can score different agent stacks:

* ``polymath-v1``            — the v1.2 stdlib harness (baseline)
* ``deepagents``             — LangChain's prebuilt deep-agent harness, used as shipped
* ``pydanticai-coder``       — Pydantic AI + pydantic-ai-harness ``Coder``, used as shipped
* ``polymath-v2``            — Polymath v2 (prebuilt SDK runtime + Polymath middleware), added later

Fairness rules (docs/research/sdk-selection.md):
1. Prebuilt stacks are used with their DEFAULT harness, plus each project's OWN
   recommended resilience configuration (retries/backoff) — never Polymath code.
2. Same model, endpoint, temperature, turn budget, workspace and verifier.
3. Token usage is read from each SDK's own usage accounting.
"""

from __future__ import annotations

import os
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# Same endpoint resolution as Polymath (POLYMATH_BASE_URL), so every stack can sit behind the
# egress governor (python -m polymath.egress) and see identical rate-limit behaviour.
NIM = os.environ.get("POLYMATH_BASE_URL", "https://integrate.api.nvidia.com/v1")
TEMPERATURE = 0.3


@dataclass
class BackendResult:
    state: str  # completed | failed
    stop_reason: str
    answer: str | None
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    duration_s: float = 0.0
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    # Shape expected by verifiers (they read .answer).
    @property
    def usage(self):  # pragma: no cover - compatibility shim
        return self


def _key() -> str:
    return os.environ["NVIDIA_NIM_API_KEY"]


# ── LangChain deepagents (prebuilt harness) ─────────────────────────────────
def run_deepagents(instruction: str, ws: Path, *, model: str, max_turns: int, **_: Any) -> BackendResult:
    from deepagents import create_deep_agent
    from deepagents.backends import LocalShellBackend
    from langchain.agents.middleware import ModelRetryMiddleware
    from langchain_openai import ChatOpenAI

    # stream_usage=True: without it LangChain reports no token usage for streamed calls (measured: 0 tokens).
    llm = ChatOpenAI(model=model, base_url=NIM, api_key=_key(), temperature=TEMPERATURE, max_retries=6, timeout=240, streaming=True, stream_usage=True)
    agent = create_deep_agent(
        model=llm,
        backend=LocalShellBackend(root_dir=str(ws), virtual_mode=False, timeout=180),
        # LangChain's own resilience layer (retries model calls incl. mid-stream errors). on_failure="error":
        # the default "continue" turns an exhausted retry into an ordinary AI message, so a run that never
        # reached the model is reported as `completed` (measured in the first bake-off).
        middleware=[ModelRetryMiddleware(max_retries=4, initial_delay=2.0, max_delay=60.0, on_failure="error")],
    )
    t0 = time.monotonic()
    msgs: list[Any] = []
    try:
        out = agent.invoke(
            {"messages": [{"role": "user", "content": f"{instruction}\n\nWorking directory: {ws}"}]},
            {"recursion_limit": max_turns * 2 + 5},
        )
        msgs = out["messages"]
        state, stop, err = "completed", "finished", None
    except Exception as e:  # recursion limit, provider errors, ...
        state, stop, err = "failed", type(e).__name__, f"{type(e).__name__}: {str(e)[:400]}"
    ai = [m for m in msgs if getattr(m, "type", "") == "ai"]
    u_in = sum((getattr(m, "usage_metadata", None) or {}).get("input_tokens", 0) for m in ai)
    u_out = sum((getattr(m, "usage_metadata", None) or {}).get("output_tokens", 0) for m in ai)
    answer = str(ai[-1].content) if ai else None
    return BackendResult(state, stop, answer, len(ai), u_in, u_out, time.monotonic() - t0, err)


# ── Pydantic AI + harness Coder (prebuilt harness) ──────────────────────────
def _pydantic_model(model: str):
    from httpx2 import AsyncClient, HTTPStatusError
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider
    from pydantic_ai.retries import AsyncHTTPX2TenacityTransport, RetryConfig, wait_retry_after
    from tenacity import retry_if_exception_type, stop_after_attempt

    def validate(resp):  # Pydantic AI docs pattern: raise on retryable status so tenacity retries
        if resp.status_code in (429, 500, 502, 503, 504):
            resp.raise_for_status()

    transport = AsyncHTTPX2TenacityTransport(
        config=RetryConfig(retry=retry_if_exception_type(HTTPStatusError), wait=wait_retry_after(max_wait=60), stop=stop_after_attempt(7), reraise=True),
        validate_response=validate,
    )
    client = AsyncClient(transport=transport, timeout=240)
    return _RequestRetryModel(OpenAIChatModel(model, provider=OpenAIProvider(base_url=NIM, api_key=_key(), http_client=client)))


def _RequestRetryModel(inner):  # noqa: N802 - factory keeps pydantic_ai imports lazy
    """Equalizer: retry a whole model request on provider errors that arrive INSIDE a 200 response.

    Pydantic AI's documented resilience (AsyncTenacityTransport) works at the HTTP-status layer, so an
    in-body error such as NIM's ``{"error": "Service temporarily overloaded"}`` is never retried and kills
    the run (measured in the smoke run). LangChain ships ModelRetryMiddleware for exactly this; to compare
    harness quality rather than provider luck, Pydantic AI gets the same policy: 4 retries, exp. backoff.
    """
    import asyncio
    import random
    from contextlib import AsyncExitStack, asynccontextmanager

    import openai
    from pydantic_ai.exceptions import ModelAPIError
    from pydantic_ai.models.wrapper import WrapperModel

    retryable = (ModelAPIError, openai.APIError)

    async def backoff(attempt: int) -> None:
        await asyncio.sleep(min(60.0, 2.0 * 2**attempt) * (0.5 + random.random()))

    class RequestRetryModel(WrapperModel):
        async def request(self, messages, model_settings, model_request_parameters):
            for attempt in range(5):
                try:
                    return await self.wrapped.request(messages, model_settings, model_request_parameters)
                except retryable:
                    if attempt == 4:
                        raise
                    await backoff(attempt)
            raise AssertionError("unreachable")

        @asynccontextmanager
        async def request_stream(self, messages, model_settings, model_request_parameters, run_context=None):
            # The provider error surfaces while the wrapped stream PEEKS its first chunk, i.e. inside
            # __aenter__, before anything reached the agent — so re-opening the stream is safe there.
            # Errors after the first chunk has been yielded are not retried (the consumer saw data).
            for attempt in range(5):
                stack = AsyncExitStack()
                try:
                    stream = await stack.enter_async_context(
                        self.wrapped.request_stream(messages, model_settings, model_request_parameters, run_context))
                except retryable:
                    await stack.aclose()
                    if attempt == 4:
                        raise
                    await backoff(attempt)
                    continue
                async with stack:
                    yield stream
                return

    return RequestRetryModel(inner)


def run_pydanticai_coder(instruction: str, ws: Path, *, model: str, max_turns: int, **_: Any) -> BackendResult:
    from pydantic_ai import Agent, UsageLimits
    from pydantic_ai_harness.coder import Coder

    agent = Agent(_pydantic_model(model), capabilities=[Coder(workspace=ws)], model_settings={"temperature": TEMPERATURE})
    t0 = time.monotonic()
    try:  # no os.chdir: it is process-global and would corrupt parallel workers; Coder scopes tools to `workspace`
        res = agent.run_sync(f"{instruction}\n\nWorking directory: {ws}", usage_limits=UsageLimits(request_limit=max_turns))
        u = res.usage
        return BackendResult("completed", "finished", str(res.output), u.requests, u.input_tokens or 0, u.output_tokens or 0, time.monotonic() - t0)
    except Exception as e:
        return BackendResult("failed", type(e).__name__, None, 0, 0, 0, time.monotonic() - t0, f"{type(e).__name__}: {str(e)[:400]}", {"trace": traceback.format_exc()[-800:]})


# ── OpenAI Agents SDK SandboxAgent (prebuilt harness) ───────────────────────
def run_openai_agents(instruction: str, ws: Path, *, model: str, max_turns: int, **_: Any) -> BackendResult:
    """SandboxAgent with its default capabilities (Filesystem, Shell, Compaction) on a local sandbox.

    Uses the Responses API, which NIM serves for Nemotron (verified) but not for GLM-5.3 (HTTP 404).
    The shipped Compaction capability delegates to server-side ``context_management``; whether a
    non-OpenAI server honours it is part of what this backend measures.
    """
    import asyncio

    from agents import ModelRetrySettings, ModelSettings, OpenAIResponsesModel, RunConfig, Runner, retry_policies
    from agents.sandbox import Manifest, SandboxAgent, SandboxRunConfig
    from agents.sandbox.sandboxes import UnixLocalSandboxClient
    from openai import AsyncOpenAI

    client = AsyncOpenAI(base_url=NIM, api_key=_key(), max_retries=6, timeout=240)
    # The SDK's own opt-in runner-managed retry: provider advice, network errors, Retry-After, 429/5xx.
    retry = ModelRetrySettings(
        max_retries=4,
        backoff={"initial_delay": 2.0, "max_delay": 60.0, "multiplier": 2.0, "jitter": True},
        policy=retry_policies.any(
            retry_policies.provider_suggested(), retry_policies.network_error(), retry_policies.retry_after(),
            retry_policies.http_status([429, 500, 502, 503, 504]),
        ),
    )
    agent = SandboxAgent(
        name="agent",
        model=OpenAIResponsesModel(model=model, openai_client=client),
        model_settings=ModelSettings(temperature=TEMPERATURE, retry=retry),
    )
    run_config = RunConfig(
        sandbox=SandboxRunConfig(client=UnixLocalSandboxClient(), manifest=Manifest(root=str(ws))),
        tracing_disabled=True,
    )
    t0 = time.monotonic()

    async def go():
        return await Runner.run(agent, f"{instruction}\n\nWorking directory: {ws}", max_turns=max_turns, run_config=run_config)

    try:
        res = asyncio.run(go())
        u = res.context_wrapper.usage
        return BackendResult("completed", "finished", str(res.final_output), u.requests, u.input_tokens, u.output_tokens, time.monotonic() - t0)
    except Exception as e:  # MaxTurnsExceeded, provider errors, ...
        return BackendResult("failed", type(e).__name__, None, 0, 0, 0, time.monotonic() - t0, f"{type(e).__name__}: {str(e)[:400]}", {"trace": traceback.format_exc()[-800:]})


# ── Polymath v2: Coder composition + measured replacements (polymath/v2/agent.py) ──
def run_polymath_v2(instruction: str, ws: Path, *, model: str, max_turns: int, terminal: bool = True, recall: bool = True, ledger: bool = True, addressable: bool = True, **_: Any) -> BackendResult:
    """Same model factory, temperature and request limit as ``pydanticai-coder``: the only
    differences are the component swaps selected by the flags."""
    from pydantic_ai import UsageLimits

    from polymath.v2.agent import V2Options, build_agent

    window = int(os.environ["POLYMATH_V2_CONTEXT_WINDOW"]) if os.environ.get("POLYMATH_V2_CONTEXT_WINDOW") else None  # stress regime
    opts = V2Options(terminal=terminal, recall=recall, ledger=ledger, addressable=addressable, context_window=window)
    agent, tel = build_agent(model, ws, opts=opts, model_obj=_pydantic_model(model))
    t0 = time.monotonic()
    extra: dict[str, Any] = {}
    try:
        res = agent.run_sync(f"{instruction}\n\nWorking directory: {ws}", usage_limits=UsageLimits(request_limit=max_turns), model_settings={"temperature": TEMPERATURE})
        u = res.usage
        out = BackendResult("completed", "finished", str(res.output), u.requests, u.input_tokens or 0, u.output_tokens or 0, time.monotonic() - t0)
        # Full transcript OUTSIDE the workspace (verifiers inspect workspaces): runs/<out>/transcripts/<task>__rN.json.
        # Needed to see WHY a context arm passed or failed, e.g. whether the model kept a fact in its own reasoning.
        tdir = ws.parent.parent / "transcripts"
        tdir.mkdir(parents=True, exist_ok=True)
        (tdir / f"{ws.name}.json").write_bytes(res.all_messages_json())
    except Exception as e:
        out = BackendResult("failed", type(e).__name__, None, 0, 0, 0, time.monotonic() - t0, f"{type(e).__name__}: {str(e)[:400]}", {"trace": traceback.format_exc()[-800:]})
    if tel.recall_runs:
        extra["recall"] = tel.recall_runs[-1].as_dict()
    extra["ledger_injections"] = len(tel.ledger_renders)
    out.extra.update(extra)
    return out


BACKENDS: dict[str, Callable[..., BackendResult]] = {
    "deepagents": run_deepagents,
    "pydanticai-coder": run_pydanticai_coder,
    "openai-agents": run_openai_agents,
    "polymath-v2": run_polymath_v2,
    # Ablations: exactly one family of swaps each, against the pydanticai-coder baseline.
    "polymath-v2-terminal": lambda *a, **k: run_polymath_v2(*a, recall=False, ledger=False, **k),
    "polymath-v2-context": lambda *a, **k: run_polymath_v2(*a, terminal=False, **k),
    # Retention arms (terminal on in all, so only context handling differs; see evals/tasks/retention.py):
    "polymath-v2-clear": lambda *a, **k: run_polymath_v2(*a, ledger=False, addressable=False, **k),  # H1 control
    "polymath-v2-recall": lambda *a, **k: run_polymath_v2(*a, ledger=False, **k),  # H1 (+H3)
}
