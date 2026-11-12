# M0 acceptance record

**Final M0 status: `ACCEPTED_WITH_RESIDUAL`**
Authority: ASTA-DECISION-003 (CEO), 2026-11-12 · Baseline reviewed: commit `2ede0fd381b0689c732e18780f14be7f446eea19`
Evidence reviewed: `docs/evidence/M0-acceptance.md` and `docs/evidence/m0-r1/*` (logs, screenshots, OKX report)
Local tag: `m0-accepted-with-residual` (annotated, local only; **not pushed**; no remote is configured)

The evidence file's own status line ("BUILT — NOT ACCEPTED") is the pre-decision status and is preserved unchanged as history; this record supersedes it for status only. No test result, skip count or evidence value was altered.

## 1. Review performed (2026-11-12, at baseline `2ede0fd`)
Independently re-run before closure (not copied from the report): working tree clean, branch `main`, no remote, no prior tags · `ruff check` + `ruff format --check` pass · `pytest` default **106 passed, 4 skipped** (4 = network opt-in) · `ASTA_RUN_NETWORK_TESTS=1 pytest` **109 passed, 1 skipped** — the one skip is the OKX live test ("OKX unreachable from this machine: ConnectError") · custom secret scan 0 findings · web `tsc` clean, 4 unit tests pass. The gitleaks (v8.30.1; 12 commits + tree), mypy (52 files), pyright, pip-audit/pnpm audit, compose PostgreSQL 16.4 migration (up/down/up) and Playwright (18/18) results are taken from the committed logs in `docs/evidence/m0-r1/`; they were produced on the same code (docs-only commits followed).

## 2. M0 gate confirmation (ASTA-M0-IMPLEMENTATION-01 §acceptance gates)
| Gate | Verdict | Basis |
|---|---|---|
| Both public-market-data adapters operate correctly | **Binance: MET (live). OKX: NOT MET live — approved residual R-1** | OKX verified offline only (REST mocks, local WebSocket reconnect/keepalive/dedupe); **OKX connectivity has NOT passed** |
| All trading execution paths disabled | MET | gateway default-DENY for every environment; no write/sign methods; route table has no order surface |
| No live execution environment exists | MET | no `LIVE` enum member; DB CHECK rejects it |
| Financial numeric tests | MET | Decimal/float rejection, floor-to-step property test, NUMERIC round-trip |
| Authentication, MFA, RBAC | MET | integration + 18 browser tests |
| Database migrations | MET | PostgreSQL 16.4 (compose) up/down/up; models == migrations |
| Market-data recovery and stale-data | MET | unit + real local WS drop/reconnect + browser nodata/aging phases |
| Duplicate-event protection | MET | unit + replay-after-reconnect |
| Clock-drift tests | MET | unit (ok/exceeded/negative/unknown=fail-closed); live Binance offset 323–853 ms |
| Security scanning without unresolved critical findings | MET | gitleaks, pip-audit, pnpm audit clean; mypy/pyright clean. Hosted CI not yet run — residual R-2 |
| Dashboard shows real public data or labels fixture | MET | browser: Binance "REAL-TIME PUBLIC DATA", OKX "FIXTURE (not real data)" |
| Zero real exchange orders submitted | MET | no code path can submit one; no authenticated exchange call exists |
| No credentials/secrets in Git history or logs | MET | gitleaks full history + tree; custom scan; logs reviewed |

All required gates other than the explicitly approved residuals are satisfied.

## 3. Residual-risk register (carried forward; none closed by this decision)
| ID | Residual | Status | Closure condition |
|---|---|---|---|
| **R-1** | **OKX public-network verification** — upstream DNS (router + ISP resolvers) returns 127.0.0.1 for `www.okx.com`/`api.binance.com`; live OKX REST/WS never reached; official OKX docs unreadable (WS URLs, keepalive, rate limits, field lists unconfirmed). Unresolved **external dependency**. No bypass attempted or authorised. | OPEN – external | Run the opt-in live tests from a lawful, permitted network (`ASTA_RUN_NETWORK_TESTS=1 uv run python -m pytest tests/contracts/test_live_public.py -rs`) and/or obtain authorised official docs. Until then OKX is "fixture/offline-verified only" and must be described that way. |
| **R-2** | **Hosted GitHub CI never executed** (`.github/workflows/ci.yml` defined; no remote; push not authorised). Every step was run locally. | OPEN – outstanding verification | First green run on a hosted runner after a separately authorised push. |
| **R-3** | **Infrastructure egress enforcement absent** — egress control is application-level only; new code using `httpx`/`websockets` directly would bypass it; no firewall/network allowlist. | OPEN – **MANDATORY GATE before any exchange credentials** | Deployment-level egress allowlist + CI rule banning direct HTTP/WS clients outside `exchanges/`, verified, before any authenticated exchange access (M3+). |
| R-4 | TOTP secrets Fernet-encrypted with a key from `ASTA_SECRET_KEY` (env), not a vault | accepted for M0 | vault/OS secret store before shared or production use |
| R-5 | Single-operator lockout (5 fails/15 min) is a self-DoS vector; no per-IP throttling | accepted | per-IP throttling / admin recovery runbook |
| R-6 | Market state in memory; symbol rules not persisted/displayed; `incidents` nothing writes; no halt/resume control | by design (M0 scope) | later milestones |
| R-7 | `/v1/audit` re-verifies the whole chain per call | accepted at M0 volume | incremental verification |
| R-8 | `pgserver` embedded Postgres is the default test DB when `ASTA_TEST_DATABASE_URL` unset | accepted | CI uses PostgreSQL 16.4 |
| R-9 | Docker Desktop on the dev PC dropped twice earlier under memory pressure | accepted | host resilience not guaranteed |
| R-10 | Browser coverage: system Chrome only; OKX browser data is fixture by design | accepted | — |

## 4. Skipped tests and numbers — stated exactly
110 collected = 49 unit + 20 contracts + 5 live_lock + 36 integration. Default: 106 passed, 4 skipped. With network enabled: 109 passed, **1 skipped (OKX live — not passed)**. Frontend 4 passed; browser 18 passed. Nothing was modified, deselected or reclassified to improve these figures.

## 5. Next milestone — M1 authorization boundary (effective only now that M0 closure is recorded)
**Authorized (when the separate M1 implementation handoff is issued):** M1 — Offline Historical Backtesting Engine, limited to historical research, data validation, strategy simulation, portfolio accounting and performance analysis.
**Not authorized by ASTA-DECISION-003, and still prohibited:** authenticated exchange access or any credentials · functioning order submission or any change to the default-DENY gateway · a paper-trading service · exchange demo/testnet trading · real-money trading · any `LIVE` environment · pushing to GitHub. R-3 must be closed before any credential is introduced. M1 must not describe OKX connectivity as passed; OKX-derived data cannot be fetched from this network (R-1).
**Status of M1 implementation: NOT STARTED.** No M1 work begins until the separate M1 implementation handoff is issued.
