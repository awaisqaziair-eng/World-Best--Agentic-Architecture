"""Run the egress governor:  python -m polymath.egress --port 8787 [--log governor.jsonl]

Then point any OpenAI-compatible client at  http://127.0.0.1:8787/v1  (e.g. POLYMATH_BASE_URL).
"""

from __future__ import annotations

import argparse
from dataclasses import fields

from aiohttp import web

from .governor import Governor, GovernorConfig


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m polymath.egress", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    defaults = GovernorConfig()
    for f in fields(GovernorConfig):
        ap.add_argument(f"--{f.name.replace('_', '-')}", type=type(getattr(defaults, f.name)) if getattr(defaults, f.name) is not None else str, default=getattr(defaults, f.name))
    ns = vars(ap.parse_args())
    host, port = ns.pop("host"), ns.pop("port")
    cfg = GovernorConfig(**ns)
    print(f"egress governor → {cfg.upstream}  listening on http://{host}:{port}{cfg.mount}  (rate {cfg.rate}/s, inflight ≤ {cfg.max_inflight})", flush=True)
    web.run_app(Governor(cfg).app(), host=host, port=port, print=None)


if __name__ == "__main__":
    main()
