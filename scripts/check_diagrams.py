"""Parse-check every Mermaid diagram in the docs with Mermaid's own parser.

    python3 scripts/check_diagrams.py            # needs node + npm (installs mermaid+jsdom into a temp dir once)

Found 2 broken diagrams (a `;` inside a sequence message terminates the statement) that GitHub would
have rendered as errors.
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(tempfile.gettempdir()) / "polymath-mermaid-check"


def main() -> int:
    diagrams = []
    for f in sorted(ROOT.glob("docs/**/*.md")) + [ROOT / "README.md"]:
        for i, m in enumerate(re.finditer(r"```mermaid\n(.*?)```", f.read_text(), re.S)):
            diagrams.append({"file": str(f.relative_to(ROOT)), "n": i + 1, "code": m.group(1)})
    CACHE.mkdir(exist_ok=True)
    if not (CACHE / "node_modules" / "mermaid").exists():
        subprocess.run(["npm", "init", "-y"], cwd=CACHE, capture_output=True, check=True)
        subprocess.run(["npm", "install", "--silent", "mermaid@11", "jsdom@25"], cwd=CACHE, check=True)
    (CACHE / "check.mjs").write_text((Path(__file__).parent / "check_mermaid.mjs").read_text())
    (CACHE / "diagrams.json").write_text(json.dumps(diagrams))
    p = subprocess.run(["node", "check.mjs", "diagrams.json"], cwd=CACHE, capture_output=True, text=True)
    print(p.stdout.strip())
    return 1 if p.returncode or "FAIL" in p.stdout else 0


if __name__ == "__main__":
    sys.exit(main())
