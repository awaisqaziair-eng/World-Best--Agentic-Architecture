# Evaluation report — nvidia/nemotron-3-super-120b-a12b (full) — v1.2 context stress: 30k window (13.6k usable)

- Date: 2026-09-25T00:59:48+00:00  ·  tasks: 8  ·  workers: 3  ·  harness mode: `full`
- **Pass rate: 8/8 = 100.0%**
- Median turns 10.5 · median tokens 60,257 · total tokens 507,964 · median wall 112.75s

## By category

| Category | Passed | Pass rate | Median turns | Median tokens |
|---|---|---|---|---|
| coding | 1/1 | 100% | 12 | 89,414 |
| data | 2/2 | 100% | 10.0 | 61,435 |
| debugging | 2/2 | 100% | 12.5 | 82,323 |
| research | 1/1 | 100% | 7 | 32,171 |
| web | 1/1 | 100% | 12 | 72,123 |
| writing | 1/1 | 100% | 5 | 26,739 |

## Tasks

| Task | Result | State / stop | Turns | Tokens | Wall s | Notes |
|---|---|---|---|---|---|---|
| code-cli-todo | ✅ | completed/finished | 12 | 89,414 | 124.8 |  |
| data-sales-report | ✅ | completed/finished | 8 | 46,843 | 105.5 |  |
| hard-sessionize | ✅ | completed/finished | 12 | 76,028 | 120.0 |  |
| debug-crash-hardening | ✅ | completed/finished | 9 | 48,392 | 57.8 |  |
| hard-bug-hunt | ✅ | completed/finished | 16 | 116,254 | 219.6 |  |
| research-multihop | ✅ | completed/finished | 7 | 32,171 | 96.3 |  |
| ops-http-service | ✅ | completed/finished | 12 | 72,123 | 179.5 |  |
| write-api-docs | ✅ | completed/finished | 5 | 26,739 | 52.3 |  |
