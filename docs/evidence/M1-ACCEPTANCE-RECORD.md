# M1 acceptance record

**Final M1 status: `ACCEPTED_WITH_RESIDUAL`**
Authority: ASTA-DECISION-004 (CEO), 2026-11-12 · Baseline reviewed: commit `7a9100d`
Evidence reviewed: `docs/evidence/M1-acceptance.md` (incl. the M1-R1 addendum), `docs/evidence/m1/*`, `docs/evidence/m1-r1/*`
Local tag: `m1-accepted-with-residual` (annotated, local only; **not pushed**; no remote is configured). The tag points at the commit that contains this record.

The status lines inside `M1-acceptance.md` ("ACCEPTANCE CANDIDATE") are the pre-decision status and are preserved unchanged as history; this record supersedes them for status only. No recorded result, checksum, test count or evidence value was altered: `git diff 4405dfb -- docs/evidence/m1` (the research result artefacts) is empty, and the M0 evidence and the M0 tag are unchanged.

## 1. Economic profitability has NOT been established
The acceptance covers the correctness, reproducibility and honesty of the offline research **engine**, not any trading strategy. Over three years of real Binance history no strategy demonstrated a validated profitable edge (evidence level NONE/WEAK): equal-weight buy-and-hold (+295.02 USDT on 1,000, itself risk-breached) beat all three active strategies; regime grid −19.07 (full period) and −35.37 (walk-forward OOS); trend pullback −39.38 / −74.83 and breached the risk budget; SMA cross +160.01 breached the risk budget, with only +9.60 in the single-use holdout and +67.56 from the one walk-forward window with admissible parameters. All figures are SIMULATED, from HISTORICAL data with ESTIMATED fills, and are not realised profit. Details: `docs/evidence/m1-r1/ECONOMIC-REPORT.md`. **Nothing in this record is a claim that any strategy is, or will be, profitable, and it supports no pilot or capital allocation.**

## 2. Closure verification performed (2026-11-12, at `7a9100d`)
Re-run independently before closure, not copied from the reports: working tree clean · no remote · `ruff check` + `ruff format --check` pass (136 files) · custom secret scan 0 findings · **gitleaks over the full history (29 commits) and tree: no leaks** · `pytest` default **351 passed, 4 skipped** (4 = network opt-in) · `ASTA_RUN_NETWORK_TESTS=1 pytest` on compose PostgreSQL 16.4: **355 passed, 0 failed, 0 skipped** · `tests/live_lock` + `tests/contracts` default **24 passed, 3 skipped**. Taken from the committed R1 evidence on the same code (only documentation commits followed): mypy strict 115 files clean, pyright 0 errors, `pip-audit` / `pnpm audit` clean, web typecheck + 7 unit tests, Playwright 30/30 (`docs/evidence/m1-r1/e2e-full-run.log`).

## 3. Acceptance gates (ASTA-M1-IMPLEMENTATION-01 / ASTA-M1-R1)
| Gate | Verdict | Basis |
|---|---|---|
| Historical data import + validation engine | MET | quality engine, provenance, content-addressed store; real Binance data all PASS |
| Simulation and portfolio ledger | MET | golden hand-calculated tests, 28 property tests, independent fill-based reconciliation exact on 7/7 real runs |
| Strategy and risk engines | MET | 3 strategies + baselines; exposure/daily-loss/drawdown limits enforced; breach reported even when profitable (real: +295.02, +160.01) |
| Research validation and comparison | MET | walk-forward with structural train/test separation, single-use holdout registry, sensitivity, comparison UI/API |
| Studies persisted with provenance, no duplicates | MET | 16 studies, migration 0003, unique content hash, append-only, 8 DB/API tests + 2 browser tests |
| Reproducibility | MET (one platform) | 7/7 full-period configurations identical across 2 runs and a fresh process / different hash seed; checksums equal the original run |
| Look-ahead protection | MET | truncation and garbage-future invariance (synthetic and real data) |
| Type checking | MET | strict mypy, no module exclusions; `src/` has no `type: ignore` |
| Python, browser, security, static checks | MET | counts in §5 |
| No validated profitable edge | **Stated, not a gate** | §1 |

## 4. M0 live-lock regression and credential / order-path confirmation
- `tests/live_lock` and `tests/contracts` pass (24 passed, 3 skipped by default; the 3 are the network opt-in tests, which also pass when enabled). Full suite 355 passed.
- `EXECUTION_ENABLED_IN_THIS_BUILD = False`; the default-deny `ExecutionGateway` is unchanged (`git diff 79f792a -- src/asta/risk src/asta/core/egress.py src/asta/core/environment.py` empty); `Environment` still has only BACKTEST / PAPER / EXCHANGE_DEMO (no LIVE).
- Source scan: no API-key / secret / passphrase / signing (HMAC) code and no order-submission endpoint in `src/` other than the egress **deny-list** entries (`/api/v3/order`, `/api/v5/trade`); settings refuse exchange-credential environment variables; the route-table test enumerates the only mutating routes (offline research + auth) and asserts no PUT/PATCH/DELETE. No authenticated exchange connection, credential, order path, paper-trading service, margin, futures or leverage exists.
- The data fetch used public unauthenticated endpoints through the egress-checked client.

## 5. Test counts, commits and limitations
**Tests:** Python 355 passed / 0 failed (network enabled), 351 passed / 4 skipped (default); by area research 189, integration 90, unit 49, contracts 20, live_lock 7 (= 355 collected). Web unit 7/7; browser 30/30 (live 16, nodata 1, aging 1, research 12). Nothing was modified, deselected or reclassified to improve these figures.

**Commits since the M0 tag (`79f792a`), all local:** `b4005c1` M1-A · `50c8fd5` M1-B · `87eb83d` + `63985ec` M1-C · `b3e083e` M1-D · `a6091e3` M1-E1 · `2faaddc` M1-E2 · `52de6e3` M1-E3 · `f9c2d5c` cleanups · `33652ef` evidence · `4405dfb` M1 acceptance document · `c6d4b9a` M1-R1-A · `412cbfc` M1-R1-B/C/D · `7a9100d` M1-R1 documents · plus the closure commit holding this record (the tagged commit).

**Known limitations:** candle-only fill estimates (no order book, queue or intrabar data); Binance rule/fee assumptions unverified; one three-year, mostly rising window and three symbols; cost sensitivity is path-dependent (a risk halt ends new buying), not additive; strategies run in-process (trusted code, not sandboxed); the holdout is single-use and multiple-comparison effects are not statistically corrected; strategy version on stored studies is the registry version at import time.

## 6. Residual-risk register (current)
| ID | Residual | Status | Closure condition |
|---|---|---|---|
| **R-1** | **OKX connectivity and historical market data.** No OKX historical data exists in M1 and no OKX research result exists. **Observation recorded at closure (not a closure of R-1):** on 2026-11-12 `www.okx.com` now resolves normally and the opt-in OKX public REST contract test (`test_okx_rest_live_or_unreachable`: server time + symbol rules) **passed** in the network-enabled runs; this passed during the M1 evidence runs too, and I did not call it out at the time — the M1 documents' statement that OKX was blocked reflects the DNS state when the data was fetched (~10:37 UTC). The OKX WebSocket and historical-candle endpoints were not exercised live, official documentation remains unconfirmed, and no OKX data was fetched or imported. | OPEN | CEO decision on whether to re-verify OKX (REST + WS + docs) and authorise an OKX historical import; until then OKX stays "offline-verified, REST contract test passes, no research data". |
| **R-2** | Hosted GitHub CI never executed (no remote; push not authorised). | OPEN | First green hosted run after a separately authorised push. |
| **R-3** | Infrastructure-level egress policy not implemented (application-level control only; new code using `httpx`/`websockets` directly would bypass it). **Mandatory gate before any exchange credentials.** | OPEN – MANDATORY GATE | Deployment egress allowlist + CI rule banning direct HTTP/WS clients outside `exchanges/`. |
| R-11 | Exchange fee schedule and trading rules are unverified assumptions (Binance rule snapshot applied to history; base-tier fees; no account tiers, BNB discount or maker/taker mix) — all research is conditional on them. | OPEN | Verified fee schedule and per-period rule history from an authoritative source. |
| R-12 | No durable research job queue or resume: queued/running runs are failed on restart and must be resubmitted. | OPEN (by design) | Persistent queue if long-running jobs become necessary. |
| R-13 | Walk-forward / sensitivity / holdout studies are generated by `scripts/run_research.py` and imported from files; they cannot be launched from the UI, and a stored study proves integrity since import, not authorship. | OPEN | UI/API-launched studies or signed artefacts. |
| R-14 | Reproducibility verified on one platform (this Windows machine, one interpreter); cross-platform not tested. | OPEN | Run the verification script on the hosted runner / Linux. |
| R-15 | The reference risk limits (5% drawdown, 2% daily loss, 10%/30% exposure) constrain even passive benchmarks: buy-and-hold breached them, so "PASSED" is rare and says more about the limits than about strategy quality. | OPEN | CEO review of limit calibration before any use beyond research (limits were deliberately not relaxed). |
| R-4 … R-10 | M0 residuals (TOTP key in env, lockout self-DoS, in-memory market state, whole-chain audit verification, embedded pgserver default test DB, Docker memory pressure, system-Chrome-only browsers) | carried, unchanged | see `M0-ACCEPTANCE-RECORD.md` |

## 7. Authorization boundaries after M1 closure
M1 closure **does not authorise**: M2 implementation · continuous paper trading or any paper-trading service · exchange demo/testnet orders · authenticated exchange connections · real-money trading · margin, futures or leverage · adding exchange credentials · GitHub push or remote creation. The default-deny execution gateway must remain unchanged; R-3 must be closed before any credential is introduced.
**Status of M2: NOT STARTED.** No M2 work begins until a separate handoff is issued.
