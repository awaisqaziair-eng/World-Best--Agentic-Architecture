"""Long-term (cross-session) memory with BM25 retrieval.

Storage is a JSONL file of records ``{id, ts, content, tags, session}``;
deletes are tombstones, so the file is append-only like the event log.
Retrieval is Okapi BM25 over lower-cased word tokens (plus tag boosts) — no
embedding service required, fully deterministic, and good enough for the
"lessons learned / user preferences / project facts" memories an agent keeps.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

_TOKEN = re.compile(r"[a-z0-9_]+")
_STOP = frozenset("a an the and or of to in on for with is are was were be been it this that as at by from".split())


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 1]


@dataclass
class MemoryRecord:
    content: str
    tags: list[str] = field(default_factory=list)
    session: str | None = None
    id: str = field(default_factory=lambda: "mem_" + uuid.uuid4().hex[:10])
    ts: float = field(default_factory=time.time)
    deleted: bool = False


class MemoryStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _load(self) -> dict[str, MemoryRecord]:
        recs: dict[str, MemoryRecord] = {}
        if not self.path.exists():
            return recs
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("deleted"):
                    recs.pop(d["id"], None)
                else:
                    recs[d["id"]] = MemoryRecord(**d)
        return recs

    def all(self) -> list[MemoryRecord]:
        with self._lock:
            return sorted(self._load().values(), key=lambda r: r.ts)

    def save(self, content: str, tags: list[str] | None = None, session: str | None = None) -> MemoryRecord:
        rec = MemoryRecord(content=content.strip(), tags=sorted({t.lower() for t in (tags or [])}), session=session)
        with self._lock, open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")
        return rec

    def delete(self, rec_id: str) -> bool:
        with self._lock:
            if rec_id not in self._load():
                return False
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"id": rec_id, "deleted": True, "content": "", "ts": time.time()}) + "\n")
        return True

    def search(self, query: str, k: int = 5, *, k1: float = 1.4, b: float = 0.75) -> list[tuple[float, MemoryRecord]]:
        recs = self.all()
        if not recs:
            return []
        q = tokenize(query)
        if not q:
            return [(0.0, r) for r in recs[-k:]][::-1]
        docs = [tokenize(r.content) + [t for tag in r.tags for t in tokenize(tag)] * 2 for r in recs]
        n = len(docs)
        avgdl = sum(len(d) for d in docs) / n or 1.0
        df = Counter(t for d in docs for t in set(d))
        scored = []
        for rec, doc in zip(recs, docs):
            tf = Counter(doc)
            dl = len(doc) or 1
            s = 0.0
            for t in q:
                if t not in tf:
                    continue
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * dl / avgdl))
            if s > 0:
                scored.append((round(s, 4), rec))
        scored.sort(key=lambda x: (-x[0], -x[1].ts))
        return scored[:k]
