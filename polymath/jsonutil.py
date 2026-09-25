"""JSON helpers: canonical serialisation, hashing and lenient parsing.

Models frequently emit *almost* JSON (code fences, trailing commas, Python
literals, single quotes). ``loads_lenient`` recovers the intended value through
a sequence of increasingly aggressive, deterministic repair passes so a single
formatting slip never costs a whole agent turn.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from typing import Any

_FENCE = re.compile(r"^\s*```(?:json|JSON|javascript|js)?\s*\n?(.*?)\n?\s*```\s*$", re.S)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")
# JSON literals appearing in value position (after ':' ',' '[' or at start), not inside words.
_JSON_LITERAL = re.compile(r"(?<![\w\"'])(true|false|null)(?![\w\"'])")
_PY_LITERALS = {"true": "True", "false": "False", "null": "None"}


class JSONRepairError(ValueError):
    """Raised when no repair pass can recover a JSON value."""


def dumps(obj: Any, *, indent: int | None = None) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=indent, default=_default)


def canonical(obj: Any) -> str:
    """Stable, whitespace-free serialisation used for hashing and cache keys."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_default)


def digest(obj: Any, n: int = 16) -> str:
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()[:n]


def _default(o: Any) -> Any:
    if hasattr(o, "to_dict"):
        return o.to_dict()
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    if isinstance(o, bytes):
        return o.decode("utf-8", "replace")
    return str(o)


def _extract_braced(text: str) -> str | None:
    """Return the first balanced {...} or [...] span, respecting strings."""
    start = None
    for i, ch in enumerate(text):
        if ch in "{[":
            start = i
            break
    if start is None:
        return None
    stack: list[str] = []
    in_str = False
    esc = False
    quote = ""
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
            continue
        if ch in "\"'":
            in_str, quote = True, ch
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if not stack or stack.pop() != ch:
                return None
            if not stack:
                return text[start : i + 1]
    return None


def loads_lenient(text: str | None, *, expect: type | None = None) -> Any:
    """Parse ``text`` as JSON, applying deterministic repairs if needed.

    Repair ladder (first success wins):
      1. strict ``json.loads``
      2. strip Markdown code fences
      3. extract first balanced object/array from surrounding prose
      4. remove trailing commas
      5. ``ast.literal_eval`` (handles single quotes, True/False/None)
    """
    if text is None:
        raise JSONRepairError("empty input")
    if not isinstance(text, str):
        return text
    raw = text.strip()
    if raw == "":
        if expect is dict:
            return {}
        raise JSONRepairError("empty input")

    candidates: list[str] = [raw]
    m = _FENCE.match(raw)
    if m:
        candidates.append(m.group(1).strip())
    braced = _extract_braced(raw)
    if braced:
        candidates.append(braced)

    errors: list[str] = []
    for cand in candidates:
        for variant in (cand, _TRAILING_COMMA.sub(r"\1", cand)):
            try:
                val = json.loads(variant)
                if expect is None or isinstance(val, expect):
                    return val
            except json.JSONDecodeError as e:
                errors.append(str(e))
            for py in (variant, _JSON_LITERAL.sub(lambda m: _PY_LITERALS[m.group(0)], variant)):
                try:
                    val = ast.literal_eval(py)
                except (ValueError, SyntaxError, MemoryError, RecursionError, TypeError):
                    continue
                if isinstance(val, (dict, list, str, int, float, bool)) or val is None:
                    if expect is None or isinstance(val, expect):
                        return val
    raise JSONRepairError(errors[0] if errors else "unparseable")
