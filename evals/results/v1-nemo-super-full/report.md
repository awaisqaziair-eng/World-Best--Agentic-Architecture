# Evaluation report — nvidia/nemotron-3-super-120b-a12b (full) — v1.1 full harness

- Date: 2026-09-25T00:26:07+00:00  ·  tasks: 28  ·  workers: 4  ·  harness mode: `full`
- **Pass rate: 26/28 = 92.9%**
- Median turns 8.5 · median tokens 54,341 · total tokens 2,238,025 · median wall 52.9s

## By category

| Category | Passed | Pass rate | Median turns | Median tokens |
|---|---|---|---|---|
| coding | 5/5 | 100% | 8 | 57,103 |
| data | 3/4 | 75% | 7.5 | 44,952 |
| debugging | 2/2 | 100% | 12.0 | 76,891 |
| extraction | 0/1 | 0% | 21 | 361,601 |
| general | 2/2 | 100% | 3.5 | 19,542 |
| git | 1/1 | 100% | 17 | 97,627 |
| math | 3/3 | 100% | 8 | 45,250 |
| ops | 4/4 | 100% | 9.5 | 69,857 |
| research | 2/2 | 100% | 7.5 | 39,623 |
| web | 2/2 | 100% | 8.5 | 47,996 |
| writing | 2/2 | 100% | 9.5 | 59,609 |

## Tasks

| Task | Result | State / stop | Turns | Tokens | Wall s | Notes |
|---|---|---|---|---|---|---|
| code-cli-todo | ✅ | completed/finished | 32 | 347,369 | 543.6 |  |
| code-intervals-tests | ✅ | completed/finished | 6 | 33,273 | 19.9 |  |
| code-lru-cache | ✅ | completed/finished | 8 | 57,103 | 33.3 |  |
| code-optimize | ✅ | completed/finished | 8 | 40,984 | 26.7 |  |
| refactor-rename | ✅ | completed/finished | 20 | 133,003 | 114.1 |  |
| data-json-flatten | ✅ | completed/finished | 11 | 66,386 | 80.9 |  |
| data-sales-report | ✅ | completed/finished | 9 | 54,773 | 77.9 |  |
| data-sqlite-analytics | ❌ | failed/model_error | 4 | 20,143 | 82.7 | answer.json unreadable: [Errno 2] No such file or directory: '/tmp/claude-0/-home-user-World-Best--Agentic-Architecture/5c299c61-7b18-5ef4-9 |
| data-svg-chart | ✅ | completed/finished | 6 | 35,132 | 29.8 |  |
| debug-crash-hardening | ✅ | completed/finished | 10 | 58,014 | 69.6 |  |
| debug-inventory | ✅ | completed/finished | 14 | 95,769 | 109.0 |  |
| extract-invoices | ❌ | failed/budget_exhausted | 21 | 361,601 | 457.6 | order of invoice_ids: expected ['PF-0007', 'BL/0093', 'KT-7781', 'DL-3310', 'NS-88', 'CN-100-A', 'PP-2025-17', 'INV-2025-0042', 'SP-5520', ' |
| general-ambiguous | ✅ | completed/finished | 4 | 18,582 | 13.4 |  |
| qa-knowledge | ✅ | completed/finished | 3 | 20,503 | 10.8 |  |
| git-feature-flow | ✅ | completed/finished | 17 | 97,627 | 83.1 |  |
| math-digit-sum | ✅ | completed/finished | 10 | 53,909 | 28.6 |  |
| math-grid-paths | ✅ | completed/finished | 8 | 42,374 | 21.6 |  |
| reason-schedule | ✅ | completed/finished | 7 | 45,250 | 51.8 |  |
| ops-archive-checksum | ✅ | completed/finished | 8 | 41,273 | 20.8 |  |
| ops-config-audit | ✅ | completed/finished | 20 | 180,785 | 164.3 |  |
| ops-log-forensics | ✅ | completed/finished | 10 | 63,978 | 104.3 |  |
| ops-organize-files | ✅ | completed/finished | 9 | 75,736 | 58.1 |  |
| research-multihop | ✅ | completed/finished | 7 | 34,959 | 18.5 |  |
| research-parallel | ✅ | completed/finished | 8 | 44,287 | 54.0 |  |
| ops-http-service | ✅ | completed/finished | 11 | 66,363 | 30.0 |  |
| web-local-docs | ✅ | completed/finished | 6 | 29,630 | 12.1 |  |
| write-api-docs | ✅ | completed/finished | 5 | 28,733 | 32.2 |  |
| write-exec-summary | ✅ | completed/finished | 14 | 90,486 | 71.0 |  |
