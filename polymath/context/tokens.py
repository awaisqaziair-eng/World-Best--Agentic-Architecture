"""Token estimation with online calibration.

We cannot ship every model's tokenizer, and exact counts are unnecessary: the
context engine needs a *conservative, self-correcting* estimate. We start from
a character heuristic and, after every model call, fold the provider-reported
``prompt_tokens`` into an exponential moving average of the true
tokens-per-estimate ratio for that model.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from ..types import Message

CHARS_PER_TOKEN = 3.4  # conservative for mixed code/prose
PER_MESSAGE_OVERHEAD = 6


def estimate_text(text: str | None) -> int:
    if not text:
        return 0
    # Non-ASCII (CJK, emoji) tokenises far worse than ASCII; count it heavier.
    non_ascii = sum(1 for ch in text if ord(ch) > 127) if len(text) < 200_000 else 0
    return int((len(text) + 2 * non_ascii) / CHARS_PER_TOKEN) + 1


def estimate_message(m: Message) -> int:
    n = PER_MESSAGE_OVERHEAD + estimate_text(m.content)
    for tc in m.tool_calls:
        n += 12 + estimate_text(tc.name) + estimate_text(json.dumps(tc.arguments, ensure_ascii=False))
    return n


def estimate_tools(tools: list[dict[str, Any]]) -> int:
    return estimate_text(json.dumps(tools, ensure_ascii=False)) if tools else 0


class Calibrator:
    def __init__(self, alpha: float = 0.3) -> None:
        self.alpha = alpha
        self.ratio: dict[str, float] = {}
        self._lock = threading.Lock()

    def observe(self, model: str, estimated: int, actual: int) -> None:
        if estimated <= 0 or actual <= 0:
            return
        r = actual / estimated
        if not 0.2 < r < 5.0:  # ignore nonsense (e.g. provider not reporting usage)
            return
        with self._lock:
            prev = self.ratio.get(model)
            self.ratio[model] = r if prev is None else (1 - self.alpha) * prev + self.alpha * r

    def adjust(self, model: str, estimated: int) -> int:
        with self._lock:
            r = self.ratio.get(model, 1.0)
        return int(estimated * max(r, 0.5))
