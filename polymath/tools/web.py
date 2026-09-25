"""fetch_url: HTTP GET with readable-text extraction (stdlib only)."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from html.parser import HTMLParser
from typing import Any

from ..jsonutil import digest
from .base import Tool, ToolContext, ToolError, ToolOutput, clip

MAX_BYTES = 4 * 1024 * 1024
_BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "header", "footer", "pre", "table", "ul", "ol", "blockquote"}
_SKIP = {"script", "style", "noscript", "svg", "head", "template", "iframe"}


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.title = ""
        self._skip = 0
        self._in_title = False
        self._href: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in _BLOCK:
            self.parts.append("\n")
        if tag in ("h1", "h2", "h3"):
            self.parts.append("#" * int(tag[1]) + " ")
        if tag == "li":
            self.parts.append("- ")
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._link_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in _BLOCK:
            self.parts.append("\n")
        if tag == "a" and self._href:
            txt = "".join(self._link_text).strip()
            if txt and not self._href.startswith(("#", "javascript:")):
                self.links.append((txt[:80], self._href))
            self._href = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._skip:
            return
        self.parts.append(data)
        if self._href is not None:
            self._link_text.append(data)

    def text(self) -> str:
        t = "".join(self.parts)
        t = re.sub(r"[ \t\r\f\v]+", " ", t)
        t = re.sub(r"\n\s*\n\s*\n+", "\n\n", t)
        return "\n".join(line.strip() for line in t.split("\n")).strip()


def html_to_text(html: str) -> tuple[str, str, list[tuple[str, str]]]:
    p = _Extractor()
    p.feed(html)
    p.close()
    return p.title.strip(), p.text(), p.links


class FetchUrlTool(Tool):
    name = "fetch_url"
    parallel_safe = True
    description = """\
HTTP GET a URL and return readable text (HTML → text with headings and links; JSON pretty-printed).
Long pages are clipped; the full text is saved to a file you can grep."""
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "max_chars": {"type": "integer", "minimum": 500, "maximum": 100000, "default": 20000},
        },
        "required": ["url"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        url = args["url"]
        if not re.match(r"^https?://", url):
            url = "https://" + url
        req = urllib.request.Request(url, headers={"User-Agent": "Polymath-Agent/1.0 (+https://github.com)", "Accept": "text/html,application/json,text/plain,*/*"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                ctype = r.headers.get("Content-Type", "")
                raw = r.read(MAX_BYTES + 1)
                status = r.status
                final_url = r.geturl()
        except urllib.error.HTTPError as e:
            body = e.read(2000).decode("utf-8", "replace")
            raise ToolError(f"HTTP {e.code} for {url}: {body[:500]}")
        except Exception as e:
            raise ToolError(f"request failed for {url}: {type(e).__name__}: {e}")
        charset = "utf-8"
        m = re.search(r"charset=([\w-]+)", ctype)
        if m:
            charset = m.group(1)
        text = raw[:MAX_BYTES].decode(charset, errors="replace")
        if "json" in ctype:
            try:
                text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
            except json.JSONDecodeError:
                pass
            header = f"URL: {final_url} (HTTP {status}, {ctype})\n\n"
        elif "html" in ctype or text.lstrip().lower().startswith(("<!doctype html", "<html")):
            title, body, links = html_to_text(text)
            link_lines = "\n".join(f"- {t}: {h}" for t, h in links[:40])
            text = body + (f"\n\nLinks:\n{link_lines}" if link_lines else "")
            header = f"URL: {final_url} (HTTP {status})\nTitle: {title}\n\n"
        else:
            header = f"URL: {final_url} (HTTP {status}, {ctype})\n\n"
        limit = int(args.get("max_chars") or 20000)
        spill = ctx.outputs_dir / f"fetch_{digest(final_url, 12)}.txt"  # stable name (hash() is salted)
        body, truncated = clip(text, limit, spill_path=spill)
        return ToolOutput(header + body, truncated=truncated, meta={"status": status, "bytes": len(raw)})
