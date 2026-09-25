---
name: technical-writing
description: Writing documentation, READMEs, reports, summaries and other prose deliverables with required structure and constraints.
---
## Checklist
1. Extract every explicit constraint first: required sections/headings (exact text and level), word/length limits, tone, audience, format (Markdown, no bullets, etc.), facts/figures that must appear.
2. Gather facts from the sources (read the code/docs); never invent APIs, parameters or numbers.
3. Structure: lead with what the reader needs most. Use the exact heading names requested.
4. Verify constraints mechanically after writing: `wc -w file`, `grep -c '^### ' file`, grep for each required figure/term, check for forbidden elements (e.g. `grep -n '^[-*] ' file` for bullets).
5. For API docs: every public function gets its signature, parameters (with types), return value, raised errors and a short example.
