# Evaluation report — z-ai/glm-5.3 (minimal) — ablation: minimal harness

- Date: 2026-09-25T00:26:07+00:00  ·  tasks: 28  ·  workers: 4  ·  harness mode: `minimal`
- **Pass rate: 28/28 = 100.0%**
- Median turns 5.0 · median tokens 15,733 · total tokens 595,323 · median wall 40.5s

## By category

| Category | Passed | Pass rate | Median turns | Median tokens |
|---|---|---|---|---|
| coding | 5/5 | 100% | 5 | 15,882 |
| data | 4/4 | 100% | 5.5 | 21,993 |
| debugging | 2/2 | 100% | 6.0 | 52,532 |
| extraction | 1/1 | 100% | 3 | 11,393 |
| general | 2/2 | 100% | 2.5 | 5,874 |
| git | 1/1 | 100% | 5 | 13,246 |
| math | 3/3 | 100% | 4 | 11,839 |
| ops | 4/4 | 100% | 4.0 | 15,395 |
| research | 2/2 | 100% | 5.0 | 16,189 |
| web | 2/2 | 100% | 7.5 | 32,006 |
| writing | 2/2 | 100% | 5.5 | 20,697 |

## Tasks

| Task | Result | State / stop | Turns | Tokens | Wall s | Notes |
|---|---|---|---|---|---|---|
| code-cli-todo | ✅ | completed/finished | 5 | 36,009 | 133.0 |  |
| code-intervals-tests | ✅ | completed/finished | 3 | 10,674 | 30.1 |  |
| code-lru-cache | ✅ | completed/finished | 7 | 31,464 | 68.9 |  |
| code-optimize | ✅ | completed/text_answer | 5 | 15,690 | 57.5 |  |
| refactor-rename | ✅ | completed/finished | 5 | 15,882 | 25.4 |  |
| data-json-flatten | ✅ | completed/finished | 5 | 19,268 | 19.7 |  |
| data-sales-report | ✅ | completed/text_answer | 6 | 24,718 | 54.5 |  |
| data-sqlite-analytics | ✅ | completed/finished | 5 | 17,203 | 34.1 |  |
| data-svg-chart | ✅ | completed/text_answer | 9 | 48,716 | 203.9 |  |
| debug-crash-hardening | ✅ | completed/text_answer | 8 | 94,072 | 195.2 |  |
| debug-inventory | ✅ | completed/text_answer | 4 | 10,993 | 19.3 |  |
| extract-invoices | ✅ | completed/finished | 3 | 11,393 | 73.4 |  |
| general-ambiguous | ✅ | completed/finished | 4 | 9,690 | 13.2 |  |
| qa-knowledge | ✅ | completed/text_answer | 1 | 2,059 | 4.5 |  |
| git-feature-flow | ✅ | completed/finished | 5 | 13,246 | 10.0 |  |
| math-digit-sum | ✅ | completed/finished | 4 | 9,654 | 11.1 |  |
| math-grid-paths | ✅ | completed/finished | 5 | 13,136 | 24.1 |  |
| reason-schedule | ✅ | completed/finished | 3 | 11,839 | 159.8 |  |
| ops-archive-checksum | ✅ | completed/finished | 5 | 15,014 | 49.3 |  |
| ops-config-audit | ✅ | completed/finished | 4 | 19,677 | 46.9 |  |
| ops-log-forensics | ✅ | completed/finished | 4 | 11,365 | 114.5 |  |
| ops-organize-files | ✅ | completed/finished | 4 | 15,776 | 17.9 |  |
| research-multihop | ✅ | completed/finished | 6 | 18,981 | 33.6 |  |
| research-parallel | ✅ | completed/finished | 4 | 13,397 | 14.4 |  |
| ops-http-service | ✅ | completed/finished | 8 | 45,339 | 403.2 |  |
| web-local-docs | ✅ | completed/finished | 7 | 18,673 | 9.8 |  |
| write-api-docs | ✅ | completed/finished | 7 | 30,506 | 90.2 |  |
| write-exec-summary | ✅ | completed/finished | 4 | 10,889 | 61.7 |  |
