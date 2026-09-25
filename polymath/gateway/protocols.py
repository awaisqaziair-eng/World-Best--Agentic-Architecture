"""Tool-call wire protocols.

The kernel speaks one canonical message format (``polymath.types.Message``).
A *protocol* translates it to and from a model's wire format:

* ``NativeProtocol`` — OpenAI-style ``tools`` / ``tool_calls`` (preferred).
* ``TextProtocol``   — tools described in the system prompt; calls emitted as
  ``<tool_call>{json}</tool_call>`` blocks and results returned as
  ``<tool_result>`` blocks. Works with *any* instruction-following model.

Decoding is deliberately forgiving: native responses that smuggle calls into
``content`` as text, ``<think>`` blocks, missing/duplicate call ids and
almost-JSON arguments are all normalised here, so the kernel never sees them.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .. import jsonutil
from ..types import Message, ToolCall

_THINK = re.compile(r"<think>(.*?)</think>", re.S)
_THINK_OPEN_ONLY = re.compile(r"^(.*?)</think>", re.S)  # template put <think> in the prompt
_TOOL_CALL_BLOCK = re.compile(r"<tool_call>\s*(.*?)\s*(?:</tool_call>|$)", re.S)
_FUNCTION_TAG = re.compile(r"<function=([\w.\-]+)>\s*(.*?)\s*</function>", re.S)


def split_reasoning(content: str | None) -> tuple[str | None, str | None]:
    """Separate inline ``<think>`` reasoning from visible content."""
    if not content:
        return content, None
    thoughts = _THINK.findall(content)
    if thoughts:
        visible = _THINK.sub("", content).strip()
        return (visible or None), "\n".join(t.strip() for t in thoughts)
    m = _THINK_OPEN_ONLY.match(content)
    if m and "<think>" not in content:
        return (content[m.end():].strip() or None), m.group(1).strip()
    return content, None


def parse_text_tool_calls(content: str | None, *, id_prefix: str = "call") -> list[ToolCall]:
    """Extract tool calls written as text. Accepts ``<tool_call>`` JSON blocks and
    ``<function=name>{json}</function>`` tags."""
    if not content:
        return []
    calls: list[ToolCall] = []
    for i, block in enumerate(_TOOL_CALL_BLOCK.findall(content)):
        if not block.strip():
            continue
        try:
            obj = jsonutil.loads_lenient(block, expect=dict)
        except jsonutil.JSONRepairError as e:
            calls.append(ToolCall(id=f"{id_prefix}_{i}", name="__unparseable__", raw_arguments=block, parse_error=str(e)))
            continue
        name = obj.get("name") or obj.get("tool") or obj.get("function") or ""
        args = obj.get("arguments", obj.get("parameters", obj.get("args", {})))
        err = None
        if isinstance(args, str):
            try:
                args = jsonutil.loads_lenient(args, expect=dict)
            except jsonutil.JSONRepairError as e:
                err, args = str(e), {}
        if not isinstance(args, dict):
            err, args = "arguments must be a JSON object", {}
        calls.append(ToolCall(id=f"{id_prefix}_{i}", name=str(name), arguments=args, raw_arguments=block, parse_error=err))
    if not calls:
        for i, (name, body) in enumerate(_FUNCTION_TAG.findall(content)):
            try:
                args = jsonutil.loads_lenient(body, expect=dict) if body.strip() else {}
                calls.append(ToolCall(id=f"{id_prefix}_{i}", name=name, arguments=args, raw_arguments=body))
            except jsonutil.JSONRepairError as e:
                calls.append(ToolCall(id=f"{id_prefix}_{i}", name=name, raw_arguments=body, parse_error=str(e)))
    return calls


def _strip_tool_blocks(content: str | None) -> str | None:
    if not content:
        return content
    out = _TOOL_CALL_BLOCK.sub("", content)
    out = _FUNCTION_TAG.sub("", out).strip()
    return out or None


def _parse_arguments(raw: Any) -> tuple[dict[str, Any], str | None, str | None]:
    """Return (arguments, raw_string, parse_error)."""
    if isinstance(raw, dict):
        return raw, None, None
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return {}, raw, None
    try:
        val = jsonutil.loads_lenient(raw, expect=dict)
        return val, raw, None
    except jsonutil.JSONRepairError as e:
        return {}, raw, f"invalid JSON arguments: {e}"


def _merge_consecutive_users(wire: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Many chat templates reject two consecutive user turns; merge them."""
    out: list[dict[str, Any]] = []
    for m in wire:
        if out and m["role"] == "user" and out[-1]["role"] == "user" and isinstance(out[-1].get("content"), str):
            out[-1] = {**out[-1], "content": out[-1]["content"] + "\n\n" + (m.get("content") or "")}
        else:
            out.append(m)
    return out


def _ensure_ids(calls: list[ToolCall], salt: str) -> list[ToolCall]:
    seen: set[str] = set()
    for i, c in enumerate(calls):
        if not c.id or c.id in seen:
            c.id = f"call_{salt}_{i}"
        seen.add(c.id)
    return calls


class NativeProtocol:
    name = "native"

    def __init__(self, *, send_reasoning_back: bool = False) -> None:
        self.send_reasoning_back = send_reasoning_back

    def encode(self, messages: list[Message], tools: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        wire: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "assistant":
                d: dict[str, Any] = {"role": "assistant", "content": m.content or ""}
                if m.tool_calls:
                    d["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
                        }
                        for tc in m.tool_calls
                    ]
                if self.send_reasoning_back and m.meta.get("reasoning"):
                    d["reasoning_content"] = m.meta["reasoning"]
                wire.append(d)
            elif m.role == "tool":
                wire.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content or ""})
            else:
                wire.append({"role": m.role, "content": m.content or ""})
        extra: dict[str, Any] = {}
        if tools:
            extra["tools"] = tools
        return _merge_consecutive_users(wire), extra

    def decode(self, raw_msg: dict[str, Any], *, salt: str) -> tuple[str | None, list[ToolCall], str | None]:
        content = raw_msg.get("content")
        if isinstance(content, list):  # content parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        reasoning = raw_msg.get("reasoning_content") or raw_msg.get("reasoning")
        content, inline_reasoning = split_reasoning(content)
        if inline_reasoning:
            reasoning = (reasoning + "\n" if reasoning else "") + inline_reasoning
        calls: list[ToolCall] = []
        for i, tc in enumerate(raw_msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            args, raw, err = _parse_arguments(fn.get("arguments"))
            calls.append(ToolCall(id=tc.get("id") or "", name=fn.get("name") or "", arguments=args, raw_arguments=raw if err else None, parse_error=err))
        if not calls and content and ("<tool_call>" in content or "<function=" in content):
            calls = parse_text_tool_calls(content, id_prefix=f"call_{salt}")
            content = _strip_tool_blocks(content)
        return content, _ensure_ids(calls, salt), reasoning


TEXT_PROTOCOL_INSTRUCTIONS = """\
# Tool calling protocol
You can call tools. To call a tool, output a block in exactly this format:

<tool_call>
{"name": "<tool_name>", "arguments": {<JSON object matching the tool's parameters>}}
</tool_call>

Rules:
- You may emit several <tool_call> blocks in one reply; they run in order.
- After your tool call blocks, STOP and wait. Results arrive in <tool_result> blocks.
- Arguments must be valid JSON (double quotes, escaped newlines inside strings).
- Never invent tool results.

# Available tools
"""


class TextProtocol:
    name = "text"

    def tool_prompt(self, tools: list[dict[str, Any]]) -> str:
        lines = [TEXT_PROTOCOL_INSTRUCTIONS]
        for t in tools:
            fn = t["function"]
            lines.append(f"## {fn['name']}\n{fn.get('description', '').strip()}\nParameters (JSON Schema): {json.dumps(fn.get('parameters', {}), ensure_ascii=False)}\n")
        return "\n".join(lines)

    def encode(self, messages: list[Message], tools: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        wire: list[dict[str, Any]] = []
        pending_results: list[str] = []

        def flush() -> None:
            if pending_results:
                wire.append({"role": "user", "content": "\n".join(pending_results)})
                pending_results.clear()

        for m in messages:
            if m.role == "tool":
                pending_results.append(f'<tool_result id="{m.tool_call_id}" name="{m.name or ""}">\n{m.content or ""}\n</tool_result>')
                continue
            flush()
            if m.role == "system":
                content = m.content or ""
                if tools:
                    content = content + "\n\n" + self.tool_prompt(tools)
                wire.append({"role": "system", "content": content})
            elif m.role == "assistant":
                text = m.content or ""
                if m.tool_calls and "<tool_call>" not in text:
                    blocks = [
                        "<tool_call>\n" + json.dumps({"name": tc.name, "arguments": tc.arguments}, ensure_ascii=False) + "\n</tool_call>"
                        for tc in m.tool_calls
                    ]
                    text = (text + "\n" if text else "") + "\n".join(blocks)
                wire.append({"role": "assistant", "content": text})
            else:
                wire.append({"role": m.role, "content": m.content or ""})
        flush()
        if tools and not any(w["role"] == "system" for w in wire):
            # No system message to extend: the tool protocol must still reach the model.
            wire.insert(0, {"role": "system", "content": self.tool_prompt(tools)})
        return _merge_consecutive_users(wire), {}

    def decode(self, raw_msg: dict[str, Any], *, salt: str) -> tuple[str | None, list[ToolCall], str | None]:
        content = raw_msg.get("content")
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        reasoning = raw_msg.get("reasoning_content") or raw_msg.get("reasoning")
        content, inline_reasoning = split_reasoning(content)
        if inline_reasoning:
            reasoning = (reasoning + "\n" if reasoning else "") + inline_reasoning
        calls = parse_text_tool_calls(content, id_prefix=f"call_{salt}")
        # Keep the full text (with blocks) so the transcript re-encodes byte-identically.
        return content, _ensure_ids(calls, salt), reasoning


def get_protocol(name: str, **kw: Any) -> NativeProtocol | TextProtocol:
    if name == "text":
        return TextProtocol()
    return NativeProtocol(**kw)
