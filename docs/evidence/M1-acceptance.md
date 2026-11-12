# ASTA M1 — Offline Historical Backtesting & Strategy Research Engine: Acceptance Evidence

**Directive:** ASTA-M1-IMPLEMENTATION-01 · **Date:** 2026-11-12 · **Base:** tag `m0-accepted-with-residual` (79f792a)

**STATUS: ACCEPTANCE CANDIDATE** — built and verified locally; **NOT accepted**. Acceptance requires CEO review. Nothing was pushed; no remote exists; M2 was not started; no M1 acceptance tag was created.
**Revision:** originally written at `4405dfb`; updated for **ASTA-M1-R1 (remediation)**. The addendum below supersedes §12, §13 and §16 of the original text where they differ.

## M1-R1 remediation addendum (2026-11-12)

Scope was remediation only. No research result was altered (every original checksum was re-derived and matched), no risk limit was changed, the default-deny gateway / egress / environment code is untouched (`git diff 4405dfb -- src/asta/risk src/asta/core/egress.py src/asta/core/environment.py` is empty), M0 evidence files and the tag `m0-accepted-with-residual` (79f792a) are unchanged, and nothing was pushed.

### R1-1. Research persistence — verification (Workstream A)

- **Gap closed:** walk-forward, sensitivity, fixed-window, final-holdout and baseline-comparison studies are now stored in `research_validations` and the Comparison page loads them from the database (previously the table was empty).
- **Schema (migration `0003`):** adds `content_hash` (unique), `sample_type`, `strategy_version`, `source_datasets` (checksum, timeframe, period, rows, root source and validation status of every input dataset), `source_artifact` (file name + SHA-256 prefix) and `generated_at`. Rows stay append-only (UPDATE / DELETE / TRUNCATE rejected by the existing trigger; tested).
- **Importer (`asta.db.research_import`, API `POST /v1/validations/import`, UI "Import studies"):** stores artifact content verbatim (canonical JSON; nothing recomputed or edited) and fails the whole import on malformed input. Only a plain directory name under `<import_dir>/studies/` is accepted (traversal rejected); RBAC `IMPORT_DATA` + CSRF + Idempotency-Key + audit entry.
- **No duplicates:** a SHA-256 over (kind, strategy, title, sample type, config, result) with a unique constraint and `ON CONFLICT DO NOTHING`. Re-import, a retry with a new key, or a different user inserts 0 rows; a replayed idempotency key returns the stored response; a *changed* artifact becomes a new row and never overwrites the old one.
- **Sample type is explicit** in list and detail: `IN_SAMPLE` (full-period sensitivity / windows / baseline comparison, with the banner "not out-of-sample evidence"), `IN_AND_OUT_OF_SAMPLE` (walk-forward; train and test columns shown separately) and `FINAL_HOLDOUT` (single evaluation, window consumed).
- **Real studies imported in the browser test:** 16 rows = 6 walk-forward + 3 sensitivity + 3 fixed-window + 3 holdout + 1 baseline.
- **Tests:** 8 backend/DB/API tests (`tests/integration/test_research_studies.py`: verbatim fidelity to the digit, provenance fields, repeat/retry, changed artifact, malformed input inserts nothing, append-only, RBAC/CSRF/idempotency/audit/traversal) + 2 new browser tests (import once; repeat is a no-op; 16 rows; sample-type cells; walk-forward detail with 7 windows and dataset provenance; holdout shows FAILED for trend pullback; sensitivity labelled in-sample; reload shows DB state; a viewer sees the studies but has no import control).
- Strategy versions are taken from the registry at import time (e.g. `regime_grid v1.0.0`); the artifacts themselves did not record a strategy version.

### R1-2. Reproducibility test results (Workstream B)

Full detail: `docs/evidence/m1-r1/reproducibility-real-data.json` (script `scripts/verify_reproducibility.py`, **overall PASS**).

| Check | Result |
|---|---|
| Same configuration run twice on the real stored data (7 configurations: all-cash, 3 passive-hold variants, SMA cross, regime grid, trend pullback): per-artefact SHA-256 of orders + events, fills, ledger entries, equity curve (26,304 points each), risk events, checkpoints, and the performance report | **identical in all 7** |
| Result checksum vs the checksum recorded by the **original** research run (a separate, earlier process) | **7/7 match** (e.g. regime grid `2456c817…`, SMA cross `3a6805de…`, trend pullback `87a2fc30…`) |
| Fresh Python process with a different `PYTHONHASHSEED` (regime grid: 2,040 fills, 14,270 ledger entries) | all six artefact digests and the checksum identical |
| Look-ahead on real data: truncating the data after 2025-04-24 (12,959 equity points compared) | fills and equity before the cut identical for regime grid (1,298 fills), SMA cross (284) and trend pullback (536) |
| Independent reconciliation (`research/validation/audit.py` re-derives balances and equity from **fill records only**, then compares with ledger entries, the last equity point and reported metrics): 7 checks per run | **7/7 runs, all checks pass** — fill-derived balances equal ledger balances exactly; equity from balances × final marks equals the last equity point; reported net P&L = last equity − 1,000; reported realised P&L = Σ fill realised P&L (SMA cross 159.94741012 both ways); reported fees ≥ quote-denominated fill fees; engine reconcile clean |
| Same checks on synthetic data (CI): 14 tests in `tests/research/test_repro_audit_r1.py`, including fresh-process / different-hash-seed, tamper detection (a corrupted fill or a wrong reported figure is caught) and end-to-end truncation invariance | pass |
| Database level: ledger entries, fills and equity read back from PostgreSQL reconcile to each other and to the stored performance report; after a simulated crash `recover_interrupted` fails only the open run (idempotent), completed runs are untouched, no partial result rows exist, and re-submitting the stored configuration reproduces the original checksum | pass (2 tests) |
| **Risk breach reported even when profitable** | real data: equal-weight passive hold **+295.02 BREACHED**, BTC-only hold +124.20 BREACHED, SMA cross **+160.01 BREACHED** (flagged `FAILED_RISK_BUDGET` in the comparison and shown "FAILED" in the UI); CI test: a +2,366 USDT synthetic run that hit a drawdown halt is reported BREACHED |

Restart / failure recovery that is implemented and tested: interrupted queued/running runs are marked FAILED at start-up with an explicit reason and can be resubmitted; cancellation is cooperative. **Not implemented:** automatic resume or a durable queue (an in-flight run is lost on restart by design).
Scope of the claim: bit-for-bit reproducibility is shown on this machine and interpreter, across process restarts and hash seeds; cross-platform reproducibility was not tested.

### R1-3. Type-checking exception report (Workstream C)

Full report: `docs/evidence/m1-r1/TYPE-SAFETY-REPORT.md`. The blanket `ignore_errors` exclusion that I disclosed in §12 has been **removed**: all 67 test-module errors were fixed in the test code. `mypy` strict: **no issues in 115 files** (src + tests, no module exclusions); `src/` has zero `type: ignore` comments and no overrides. The only remaining relaxations are the M0 `tests.*` untyped-def settings (test bodies are still checked) and six deliberate `type: ignore` lines in tests, all listed in the report.

### R1-4. Consolidated economic evaluation (Workstream D)

Full report: `docs/evidence/m1-r1/ECONOMIC-REPORT.md` (generated by `scripts/economic_report.py` from the stored artefacts; benchmark rows for the holdout and walk-forward windows re-run the same engine on the same data). It covers 1,000 USDT initial capital, full-period, walk-forward OOS and holdout results, cash and buy-and-hold benchmarks, fees and slippage, drawdown, trading frequency, risk violations, inventory, data provenance, exchange assumptions and the reasons for each pass/fail. Headlines (all simulated):

- Full period: equal-weight buy-and-hold +295.02 (BREACHED) beat SMA cross +160.01 (BREACHED), regime grid −19.07 (PASSED) and trend pullback −39.38 (BREACHED).
- Walk-forward OOS (strict rule): regime grid −35.37, trend pullback −74.83, SMA cross +67.56 from the only window with admissible parameters; the passive benchmark over the same windows was +36.65 and breached the risk budget in 7/7 windows.
- Final holdout (180 days, once): regime grid −14.41 PASSED, trend pullback −37.11 BREACHED, SMA cross +9.60 PASSED, passive hold +5.21 BREACHED.
- Modelled fees + slippage: SMA cross 40.70, regime grid 42.30, trend pullback 80.00 USDT, against gross trading P&L of 200.70 / 23.23 / 40.61.
- **Corrections to my earlier text:** §8 said costs hurt "monotonically"; that holds for SMA cross and regime grid but **not** for trend pullback (fees ×3 −37.70 vs ×1 −39.38), and cost scenarios are path-dependent because a risk halt ends new buying. SMA cross did beat the passive benchmark in the holdout and in the one admissible walk-forward window; neither is evidence of an edge.
- Conclusion unchanged: **no demonstrated profitable edge; evidence level NONE/WEAK.**

### R1-5. Full regression test counts

- Python: **355 passed, 0 failed** with `ASTA_RUN_NETWORK_TESTS=1` on compose PostgreSQL 16.4; the default run is **351 passed, 4 skipped**. By area: research 189, integration 90, unit 49, contracts 20, live-lock 7 (M1 closed at 331; R1 adds 24: 8 study persistence, 14 reproducibility/audit, 2 DB reconcile/recovery).
- Web: unit 7/7, typecheck clean, `next build` OK. Browser (Playwright, system Chrome): live 16 + nodata 1 + aging 1 + research **12** = **30/30 passed** (`docs/evidence/m1-r1/e2e-full-run.log`).
- Static: ruff check + format clean; pyright 0 errors; mypy strict clean (115 files); `pip-audit` and `pnpm audit` no known vulnerabilities; secret scan 0 findings; gitleaks over the full history (28 commits at `412cbfc`): no leaks.

### R1-6. Browser acceptance screenshots

All run output now goes to `docs/evidence/m1-r1/screenshots/` (the frozen M0 folder is no longer written). New: `18-research-stored-walkforward.png` (stored walk-forward study with sample-type banner, dataset provenance and separate in-sample / out-of-sample columns) and `19-research-stored-sensitivity.png`. Screenshots 01–17 are re-captured there from the final build.

### R1-7. Security and M0 live-lock regression

M0 live-lock, import-boundary, egress, credential and route tests all pass (`tests/live_lock` + `tests/contracts`: 24 passed, 3 skipped; full suite 355 passed). One deliberate edit to an M0-era test: `tests/integration/test_api.py` enumerates the only mutating routes allowed, and `POST /v1/validations/import` (local-file study persistence: no exchange, no order, no funds) was added to that explicit list, as the M1 research routes were. The "no PUT/PATCH/DELETE" assertion and every banned-word route check are unchanged. No new network access, credentials or execution capability; the endpoint reads only local files under the studies directory and is RBAC/CSRF/idempotency/audit protected.

### R1-8. Changes and local commits (no push)

| Commit | Content |
|---|---|
| `c6d4b9a` | R1-A: migration 0003, research_import module, API, Comparison page, backend/DB/browser tests, screenshots moved to m1-r1 |
| `412cbfc` | R1-B/C/D: independent audit module, reproducibility tests and real-data verification, strict mypy restored, economic report, migration constraint fix |
| (next commit) | this document and the type-safety report |

Operational note: the compose test database had to be reset once because I edited migration 0003 (unique index → unique constraint) after it had been applied to that disposable DB. No real data was involved.

### R1-9. Remaining residual risks

All of §16 still applies except items 7 (persistence) and 9 (mypy relaxation), which are closed. Additionally: (a) walk-forward / sensitivity studies are produced by the research script, not launchable from the UI, and a stored study proves integrity since import (hash), not who authored the file; (b) there is no durable job queue or automatic resume; (c) the strategy version on stored studies is the registry's version at import time; (d) reproducibility is shown on one platform; (e) the 5% drawdown / 2% daily-loss limits are strict for a volatile asset class: even buy-and-hold breaches them, so "PASSED" is rare and says more about the limits than about strategy quality; (f) rule/fee assumptions remain unverified and OKX has no data (M0 residual R-1); (g) hosted CI has still not run.

### R1-10. Acceptance recommendation

**ACCEPTANCE CANDIDATE** (not accepted; no tag created). The three engineering gaps I flagged (persistence, type-check exclusion, reproducibility evidence) are closed with automated evidence, all original results were reproduced bit-for-bit, and the economic report preserves every negative finding. Recommended CEO focus: the unverified exchange rule/fee assumptions (a verification source is needed before any use beyond research), the risk-limit calibration finding in R1-9(e), and the fact that no strategy shows an edge, which should inform any M2 scoping but does not affect the correctness of the M1 engine. An acceptance tag must await separate CEO approval.

---

> All results below are **SIMULATED, from HISTORICAL DATA, with ESTIMATED FILLS. NOT LIVE TRADING.** Nothing here is realised profit.
> **No strategy tested showed evidence of a profitable edge.** Acceptance of M1 is about the engine's correctness and honesty, not strategy performance.

## 1. Implementation summary

M1-A..E delivered an offline research engine: local-file data import with quality validation and provenance; resampling; a deterministic event-driven simulator with conservative candle-only fills, an immutable ledger and a risk engine; three strategy families plus baselines; metrics with an explicit N/A policy; walk-forward with a structural holdout; fee/slippage/latency/missing-data sensitivity; a deterministic rule-based advisor; PostgreSQL persistence with immutability triggers; a bounded background worker; a research REST API (RBAC, CSRF, idempotency, audit); and four research web pages (Datasets, Strategy lab, Results, Comparison). Strategies cannot reach any exchange; the M0 default-deny `ExecutionGateway` is unchanged.

## 2. Architecture / ADR changes

- ADR-0005 research-module purity and reuse (`asta.research` imports only `asta.core`, `asta.risk.policy`, itself; a test bans network/DB/subprocess/asyncio imports)
- ADR-0006 simulation semantics (closed-bar causality; next-open activation; conservative fills)
- ADR-0007 accounting and risk definitions (ledger identity, caps, breakers)
- ADR-0008 data policy, persistence, validation
- `docs/architecture/m1-plan.md`: module map and 24-category test matrix.
- Refactor: shared API dependencies moved to `api/deps.py`. No ADR 0001–0004 decision was altered.

## 3. Module inventory

`src/asta/research/`: `types`, `runner`, `advisory`; `data/` (models, checksum, importer, quality, resample, profiles, fixtures, store); `sim/` (orders, ledger, risk, execution, context, engine); `strategies/` (base, indicators, grid "regime grid", trend_pullback, baselines, registry); `analytics/` (metrics, regimes); `validation/` (splits, walkforward, holdout, sensitivity, compare). Plus `research_jobs.py` (worker), `api/research.py`, `db/research_models.py`, `db/research_store.py`, `core/money.py` helpers, new RBAC permissions.

## 4. DB migration inventory

`0002_m1_research` adds 16 tables: historical_datasets, historical_dataset_versions, dataset_quality_reports, strategy_definitions, strategy_versions, backtest_run_configs, backtest_runs, simulated_orders, simulated_fills, simulated_ledger_entries, portfolio_equity_snapshots, risk_events, performance_reports, research_validations, research_holdout_windows, idempotency_keys. A trigger function makes result, dataset-version, quality, validation, holdout and config tables append-only (UPDATE/DELETE/TRUNCATE rejected); `backtest_runs` are immutable once COMPLETED/FAILED/CANCELLED and not deletable. Migration down/up is tested.

## 5. API and frontend inventory

API (all under `/v1`): GET `datasets`, `datasets/importable`, `datasets/{id}/quality`, `strategies`, `profiles`, `backtests`, `backtests/{id}` (+`/results`, `/equity`, `/trades` = simulated fills, `/risk`), `validations`, `validations/{id}`; POST `datasets/import`, `datasets/{id}/validate`, `datasets/{id}/derive`, `backtests`, `backtests/{id}/cancel`. Permissions `VIEW_RESEARCH`, `RUN_RESEARCH`, `IMPORT_DATA`; POSTs need CSRF + Idempotency-Key and are audited. Import accepts only a plain file name within the configured import directory. The route live-lock test uses an explicit allowlist and asserts no PUT/PATCH/DELETE or order/funds routes exist.
Frontend: `/research` (overview), `/research/datasets`, `/lab`, `/results`, `/compare`; every page shows the SIMULATED / HISTORICAL DATA / ESTIMATED FILLS / NOT LIVE TRADING banner; no execution controls; exports JSON/CSV.

## 6. Data quality and provenance

Real public Binance 5m klines (BTC/ETH/SOL-USDT, 2023-11-01 → 2026-11-01, 315,648 rows each) were fetched through the existing egress-checked public client (no credentials), saved to `data/import/`, imported from local files, validated (all PASS: gaps, duplicates, OHLC consistency, outliers, timestamps) and resampled to 15m/1h/4h/1d (complete buckets only; 0 dropped). Datasets are content-addressed (SHA-256) with parent links; derived data carries its root source label. `data/` is git-ignored. Report: `docs/evidence/m1/data-report.json`. **OKX: no data** (upstream DNS block; not bypassed, M0 residual R-1 stands). Exchange rule set is a current snapshot applied to history and fee profiles are standard base-tier assumptions — both **unverified assumptions**, labelled as such in the UI.

## 7. Strategy behaviour and simulation assumptions

Strategies: regime grid, trend pullback, SMA-cross baseline, passive hold, all cash. See ADR-0006/0007. Key assumptions: decisions at bar close activate at next open (+latency); limit fills need trade-through by ≥1 tick, no queue-priority claim; stop wins ties; no intrabar round trips; gaps expire pending orders; participation cap; exposure caps 10% per asset / 30% total; daily-loss breaker 2%; drawdown halt 5% (a breach is reported as a failed risk budget whatever the P&L).

Full period, 3 years, 1000 USDT (`baseline-comparison.json`):

| Run | Net P&L (USDT) | Notes |
|---|---|---|
| all cash | 0 | |
| passive hold, equal-weight (300) | +295.02 | max DD 42.6%, risk budget BREACHED |
| passive hold, BTC only (100) | +124.20 | BREACHED |
| passive hold, full capital (unconstrained reference) | +494.25 | DD 51%, not risk-compliant |
| sma_cross | +160.01 | BREACHED (5.4% DD) |
| regime_grid | −19.07 | risk budget PASSED |
| trend_pullback | −39.38 | BREACHED |

Buy-and-hold beat every active strategy and the period is mostly a rising market; sma_cross's gain also failed the risk budget.

## 8. Fee / slippage sensitivity

`sensitivity.json`: costs hurt monotonically (sma_cross +160 → +56 at 3× fees). regime_grid is **unstable**: its sign flips with +1–2 bars of latency (+9, +55) and with 5% missing data (+69). That is an instability finding, not robustness.

## 9. Ledger reconciliation evidence

Immutable entries; weighted-average cost; quantum 1e-8 (buy notional/fees round up, sell down); exact identity `equity − initial = realized + unrealized + corrections`. Golden hand-calculated engine tests and Hypothesis property tests (28 properties) assert cash/position/reservation conservation, identity at every step and non-negative balances; DB round-trip test confirms the stored result checksum is value-based and reproducible.

## 10. Look-ahead tests

Truncation invariance (decisions at bar *t* identical when all future bars are removed), garbage-future invariance (future bars replaced with noise), indicator causality tests, next-open activation tests, and a walk-forward test asserting train windows cannot read test bars (`select_bars`/`period_end`). All pass.

## 11. Walk-forward results

7 windows, train 270 d / test 90 d, last 180 d held out. Pooled out-of-sample: regime_grid −35.37 (2/7 windows positive); trend_pullback −74.83 strict (3/7), −109.67 flagged variant; sma_cross strict: only 1/7 windows admissible (+67.56); flagged variant −11.79 with 3 risk-breached OOS windows. Final holdout, evaluated **once**: regime_grid −14.41 (risk PASSED); trend_pullback −37.11 (BREACHED); sma_cross +9.60 (PASSED, tiny). Files: `docs/evidence/m1/walk-forward-*.json`, `final-holdout.json`. **Evidence level: NONE/WEAK. Profitability is not established.**

## 12. Automated test results (2026-11-12, HEAD 33652ef)

- Python: **331 passed, 0 failed** (with `ASTA_RUN_NETWORK_TESTS=1`, compose PostgreSQL 16.4 on :5641): research 175, integration 80, unit 49, contracts 20, live-lock 7. (Default run without network tests: 326 passed, 4 skipped, from the previous full run.)
- Web unit: all pass (0 fail); typecheck clean; `next build` OK.
- Browser (Playwright, system Chrome): live 16 + nodata 1 + aging 1 + research 10 = **28/28 passed**; log `docs/evidence/m1/e2e-full-run.log`.
- Static: ruff check + format clean; pyright 0 errors; mypy strict **no issues in 111 files** (container; `docs/evidence/m1/mypy.log`); pip-audit and pnpm audit: no known vulnerabilities; secret scan 0 findings; gitleaks (container): 22 commits at the time, no leaks.
- *(Superseded by R1-3: the mypy test-module exclusion described here was removed and all 67 errors were fixed.)*

## 13. Browser acceptance screenshots

`docs/evidence/m1/screenshots/`: 13-research-datasets, 14-research-lab, 15-research-compare, 16-research-results, 17-research-results-mobile (M0 screenshots untouched). The browser tests use **synthetic** fixtures (labelled "SYNTHETIC (not real exchange data)"), not the real data. They cover import, derive, run, compare, results, export, cancel, N/A rendering, viewer read-only, no-execution-controls scan, no-profit-claim scan and 390 px no-overflow (a lab-page overflow found here was fixed in CSS).

## 14. Security and live-lock regression

M0 live-lock, import-boundary, egress, credential-refusal and route-allowlist tests pass unchanged apart from the explicit research allowlist; research modules are import-boundary tested; `Environment` still has no LIVE; `ExecutionGateway` still default-deny. No credentials, authenticated connectivity or order submission were added. Data fetch used public unauthenticated endpoints through the allowlist only.

## 15. Git commits (local only; no push)

b4005c1 M1-A · 50c8fd5 M1-B · 87eb83d M1-C · 63985ec lint · b3e083e M1-D · a6091e3 M1-E1 · 2faaddc M1-E2 · 52de6e3 M1-E3 · f9c2d5c cleanups/bulk insert/lint · 33652ef evidence. This document is committed after 33652ef.

## 16. Known limitations / residual risks

1. Candle-only fill model: fills are estimates; no order-book, queue or intrabar information.
2. Exchange rules and fee schedules are unverified assumptions; no account-specific tiers.
3. OKX has no data (R-1 residual); results cover Binance spot only.
4. One 3-year window, mostly bullish; three symbols; results are regime-dependent.
5. Strategies run in-process; they are trusted code, not sandboxed (a custom strategy could misbehave, though the import-boundary test and the absence of any network path limit the blast radius).
6. Holdout was evaluated once for one parameter-selection cycle; the multiple-comparison burden of 3 strategies × variants is not corrected statistically.
7. ~~Research validations not persisted~~ closed in M1-R1 (see R1-1).
8. Hosted CI has not run (M0 residual); all verification is local. mypy/gitleaks run in containers because of Windows App Control.
9. ~~mypy relaxation on research tests~~ closed in M1-R1 (see R1-3).
10. Windows CRLF/LF warnings from git on checkout; no functional effect.

## 17. Acceptance recommendation

**ACCEPTANCE CANDIDATE.** The engine is deterministic, causal, reconciles exactly, persists immutably, enforces risk budgets, and reports losses and instability without embellishment. Recommended CEO review focus: (a) the unverified rule/fee assumptions, (b) and (c) were closed by M1-R1. The honest research conclusion is that **no tested strategy demonstrated a robust edge**; this is not a blocker for M1 acceptance but should inform any M2 scoping. Do not treat any figure here as expected live performance.
