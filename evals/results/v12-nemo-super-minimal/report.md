# Evaluation report — nvidia/nemotron-3-super-120b-a12b (minimal) — v1.2 ablation: minimal harness

- Date: 2026-09-25T00:57:11+00:00  ·  tasks: 28  ·  workers: 3  ·  harness mode: `minimal`
- **Pass rate: 22/28 = 78.6%**
- Median turns 8.0 · median tokens 28,613 · total tokens 1,361,503 · median wall 59.4s

## By category

| Category | Passed | Pass rate | Median turns | Median tokens |
|---|---|---|---|---|
| coding | 4/5 | 80% | 7 | 20,117 |
| data | 4/4 | 100% | 10.5 | 38,229 |
| debugging | 2/2 | 100% | 9.0 | 31,461 |
| extraction | 0/1 | 0% | 20 | 369,656 |
| general | 2/2 | 100% | 4.0 | 20,444 |
| git | 1/1 | 100% | 13 | 39,878 |
| math | 3/3 | 100% | 8 | 23,664 |
| ops | 1/4 | 25% | 9.0 | 29,836 |
| research | 2/2 | 100% | 8.0 | 24,142 |
| web | 2/2 | 100% | 3.5 | 9,292 |
| writing | 1/2 | 50% | 10.5 | 47,514 |

## Tasks

| Task | Result | State / stop | Turns | Tokens | Wall s | Notes |
|---|---|---|---|---|---|---|
| code-cli-todo | ❌ | completed/finished | 13 | 117,753 | 97.0 | `todo.py add Read` output: expected 'Added #3', got 'Added #2'; `todo.py list` output: expected '1. [x] Buy milk\n3. [ ] Read', got '1. [x]  |
| code-intervals-tests | ✅ | completed/finished | 6 | 19,861 | 23.2 |  |
| code-lru-cache | ✅ | completed/finished | 5 | 15,533 | 24.7 |  |
| code-optimize | ✅ | completed/finished | 7 | 20,117 | 41.4 |  |
| refactor-rename | ✅ | completed/finished | 18 | 80,874 | 91.3 |  |
| data-json-flatten | ✅ | completed/finished | 10 | 34,707 | 82.2 |  |
| data-sales-report | ✅ | completed/finished | 8 | 31,665 | 146.6 |  |
| data-sqlite-analytics | ✅ | completed/finished | 11 | 41,751 | 143.7 |  |
| data-svg-chart | ✅ | completed/finished | 11 | 45,200 | 95.8 |  |
| debug-crash-hardening | ✅ | completed/finished | 9 | 29,481 | 39.1 |  |
| debug-inventory | ✅ | completed/finished | 9 | 33,441 | 28.7 |  |
| extract-invoices | ❌ | failed/budget_exhausted | 20 | 369,656 | 377.1 | invoice count: expected 10, got 9; order of invoice_ids: expected ['PF-0007', 'BL/0093', 'KT-7781', 'DL-3310', 'NS-88', 'CN-100-A', 'PP-2025 |
| general-ambiguous | ✅ | completed/finished | 4 | 9,855 | 16.1 |  |
| qa-knowledge | ✅ | completed/text_answer | 4 | 31,034 | 24.4 |  |
| git-feature-flow | ✅ | completed/finished | 13 | 39,878 | 55.0 |  |
| math-digit-sum | ✅ | completed/finished | 8 | 23,664 | 45.0 |  |
| math-grid-paths | ✅ | completed/finished | 9 | 27,745 | 69.3 |  |
| reason-schedule | ✅ | completed/finished | 3 | 13,553 | 63.8 |  |
| ops-archive-checksum | ✅ | completed/finished | 8 | 23,507 | 50.7 |  |
| ops-config-audit | ❌ | failed/model_error | 2 | 6,623 | 85.7 | audit.txt: expected ['svc-billing-32', 'svc-cart-14', 'svc-cart-22', 'svc-cart-38', 'svc-geo-06', 'svc-notify-04', 'svc-search-35'], got Non |
| ops-log-forensics | ❌ | completed/finished | 10 | 36,166 | 186.6 | top5xx.txt: expected ['10.0.188.6 71', '10.3.226.9 71', '10.0.7.95 70', '10.3.144.123 69', '10.1.119.178 64'], got ['10.2.254.178 296', '10. |
| ops-organize-files | ❌ | completed/finished | 19 | 147,542 | 174.0 | manifest entry count: expected 60, got 61 |
| research-multihop | ✅ | completed/finished | 8 | 22,023 | 23.9 |  |
| research-parallel | ✅ | completed/finished | 8 | 26,261 | 23.8 |  |
| ops-http-service | ✅ | failed/model_error | 1 | 2,986 | 82.7 |  |
| web-local-docs | ✅ | completed/finished | 6 | 15,599 | 11.5 |  |
| write-api-docs | ✅ | completed/finished | 6 | 23,479 | 36.7 |  |
| write-exec-summary | ❌ | failed/budget_exhausted | 15 | 71,549 | 106.1 | word count 116 not in 120-170 |
