# Evaluation report — z-ai/glm-5.3 (full) — v1.2 full harness (streaming + overhead reductions)

- Date: 2026-09-25T00:50:21+00:00  ·  tasks: 28  ·  workers: 3  ·  harness mode: `full`
- **Pass rate: 19/28 = 67.9%**
- Median turns 5.0 · median tokens 20,040 · total tokens 746,239 · median wall 62.5s

## By category

| Category | Passed | Pass rate | Median turns | Median tokens |
|---|---|---|---|---|
| coding | 5/5 | 100% | 7 | 37,661 |
| data | 4/4 | 100% | 6.5 | 37,595 |
| debugging | 2/2 | 100% | 6.5 | 43,080 |
| extraction | 0/1 | 0% | 0 | 0 |
| general | 0/2 | 0% | 0.0 | 0 |
| git | 1/1 | 100% | 4 | 15,034 |
| math | 2/3 | 67% | 5 | 17,595 |
| ops | 4/4 | 100% | 5.0 | 28,365 |
| research | 0/2 | 0% | 0.0 | 0 |
| web | 1/2 | 50% | 5.0 | 46,924 |
| writing | 0/2 | 0% | 0.0 | 0 |

## Tasks

| Task | Result | State / stop | Turns | Tokens | Wall s | Notes |
|---|---|---|---|---|---|---|
| code-cli-todo | ✅ | completed/finished | 8 | 89,185 | 682.4 |  |
| code-intervals-tests | ✅ | completed/finished | 7 | 37,661 | 68.2 |  |
| code-lru-cache | ✅ | completed/finished | 8 | 60,471 | 140.7 |  |
| code-optimize | ✅ | completed/finished | 6 | 26,141 | 34.7 |  |
| refactor-rename | ✅ | completed/finished | 4 | 17,549 | 16.3 |  |
| data-json-flatten | ✅ | completed/text_answer | 5 | 26,923 | 52.3 |  |
| data-sales-report | ✅ | completed/finished | 8 | 48,268 | 80.7 |  |
| data-sqlite-analytics | ✅ | completed/finished | 5 | 26,283 | 76.2 |  |
| data-svg-chart | ✅ | completed/finished | 10 | 65,518 | 108.9 |  |
| debug-crash-hardening | ✅ | completed/text_answer | 9 | 65,758 | 144.6 |  |
| debug-inventory | ✅ | completed/text_answer | 4 | 20,402 | 33.8 |  |
| extract-invoices | ❌ | failed/model_error | 0 | 0 | 40.6 | invoices.json unreadable: [Errno 2] No such file or directory: '/tmp/claude-0/-home-user-World-Best--Agentic-Architecture/5c299c61-7b18-5ef4 |
| general-ambiguous | ❌ | failed/model_error | 0 | 0 | 69.2 | no config file with port 8080 and debug disabled; final answer should name the file created |
| qa-knowledge | ❌ | failed/model_error | 0 | 0 | 62.9 | must state O(n log n); must state heapsort is not stable |
| git-feature-flow | ✅ | completed/text_answer | 4 | 15,034 | 32.8 |  |
| math-digit-sum | ✅ | completed/finished | 5 | 17,595 | 75.0 |  |
| math-grid-paths | ✅ | completed/finished | 5 | 19,679 | 25.5 |  |
| reason-schedule | ❌ | failed/model_error | 1 | 4,919 | 92.8 | schedule.json unreadable: [Errno 2] No such file or directory: '/tmp/claude-0/-home-user-World-Best--Agentic-Architecture/5c299c61-7b18-5ef4 |
| ops-archive-checksum | ✅ | completed/finished | 5 | 38,470 | 698.3 |  |
| ops-config-audit | ✅ | completed/finished | 4 | 15,805 | 13.7 |  |
| ops-log-forensics | ✅ | completed/finished | 6 | 24,922 | 36.2 |  |
| ops-organize-files | ✅ | completed/finished | 5 | 31,808 | 50.8 |  |
| research-multihop | ❌ | failed/model_error | 0 | 0 | 51.1 | answer.json unreadable: [Errno 2] No such file or directory: '/tmp/claude-0/-home-user-World-Best--Agentic-Architecture/5c299c61-7b18-5ef4-9 |
| research-parallel | ❌ | failed/model_error | 0 | 0 | 55.0 | answers.json unreadable: [Errno 2] No such file or directory: '/tmp/claude-0/-home-user-World-Best--Agentic-Architecture/5c299c61-7b18-5ef4- |
| ops-http-service | ✅ | completed/finished | 10 | 93,848 | 735.9 |  |
| web-local-docs | ❌ | failed/model_error | 0 | 0 | 42.1 | answer.json unreadable: [Errno 2] No such file or directory: '/tmp/claude-0/-home-user-World-Best--Agentic-Architecture/5c299c61-7b18-5ef4-9 |
| write-api-docs | ❌ | failed/model_error | 0 | 0 | 65.2 | API.md missing |
| write-exec-summary | ❌ | failed/model_error | 0 | 0 | 62.1 | summary.md missing |
