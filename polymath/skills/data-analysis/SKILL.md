---
name: data-analysis
description: Analysing CSV/JSON/log/SQLite data - aggregation, statistics, cleaning, transformations, reports with exact numbers.
---
## Workflow
1. **Profile first**: `head -5 file`, `wc -l`, column names, types, delimiters, header presence, missing/malformed values, encodings.
2. **Write a script** (python3 heredoc or a .py file) rather than eyeballing. Use `csv.DictReader`, `json`, `sqlite3`, `statistics`, `collections.Counter/defaultdict`, `datetime`. (pandas may not be installed; the stdlib is enough.)
3. **Handle dirty data explicitly**: decide and state the rule for missing values, malformed rows, duplicates, whitespace, case, thousands separators, currency symbols. Count what you dropped.
4. **Compute exactly**: use `decimal.Decimal` or round only at the end; keep full precision in intermediate steps.
5. **Cross-check** every headline number a second way (e.g. totals by group must sum to the grand total; counts must match `wc -l` minus header minus dropped rows).
6. **Write outputs in exactly the requested format** (keys, ordering, rounding, file name). Validate by reading the output file back.

## Shell one-liners
- Top-N counts: `awk '{print $1}' f | sort | uniq -c | sort -rn | head`
- Filter by field: `awk -F, '$3 > 100' f.csv`
- SQLite from Python: `python3 -c "import sqlite3; c=sqlite3.connect('db'); print(c.execute('select name from sqlite_master').fetchall())"`
