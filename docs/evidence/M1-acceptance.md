# ASTA M1 — Offline Historical Backtesting & Strategy Research Engine: Acceptance Evidence

**Directive:** ASTA-M1-IMPLEMENTATION-01 · **Date:** 2026-11-12 · **Base:** tag `m0-accepted-with-residual` (79f792a)

**STATUS: ACCEPTANCE CANDIDATE** — built and verified locally; **NOT accepted**. Acceptance requires CEO review. Nothing was pushed; no remote exists; M2 was not started.

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
- **Disclosed relaxation:** mypy `ignore_errors` was set for `tests.research.*` and `tests.integration.test_research_db` (67 typing errors in loosely typed Hypothesis/parametrised helpers). `src/` remains fully strict. Also `E501` is ignored for migrations.

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
7. Research validations (walk-forward/sensitivity) exist as JSON evidence files; they are not yet loaded into `research_validations`, so the Comparison page's "stored studies" table is empty.
8. Hosted CI has not run (M0 residual); all verification is local. mypy/gitleaks run in containers because of Windows App Control.
9. The mypy relaxation on research tests (see §12).
10. Windows CRLF/LF warnings from git on checkout; no functional effect.

## 17. Acceptance recommendation

**ACCEPTANCE CANDIDATE.** The engine is deterministic, causal, reconciles exactly, persists immutably, enforces risk budgets, and reports losses and instability without embellishment. Recommended CEO review focus: (a) the unverified rule/fee assumptions, (b) whether to load the research validations into the DB before acceptance, (c) the disclosed mypy relaxation. The honest research conclusion is that **no tested strategy demonstrated a robust edge**; this is not a blocker for M1 acceptance but should inform any M2 scoping. Do not treat any figure here as expected live performance.
