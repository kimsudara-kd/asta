# M1 implementation plan and acceptance test matrix (ASTA-M1-IMPLEMENTATION-01)

Baseline: tag `m0-accepted-with-residual` = `79f792a` (clean tree, no remote). Gateway: default DENY, `EXECUTION_ENABLED_IN_THIS_BUILD=False` (verified). Existing assets reused: see ADR-0005. Potential duplicates and decisions: research `Bar` vs `CandleEvent` (slim vs event contract, converter provided); `RuleProfile` wraps `SymbolRules` + versioning/verification; `SimRiskLimits` takes its default percentages from `RiskPolicy`.

## Module map (all under `src/asta/`)
| Capability | Package / file |
|---|---|
| M1.01 data | `research/data/{models,checksum,importer,resample,profiles,fixtures,store}.py` |
| M1.02 quality | `research/data/quality.py` |
| M1.03 strategies | `research/strategies/{base,indicators,grid,trend_pullback,baselines,registry}.py` |
| M1.04 simulation | `research/sim/{orders,execution,engine,context}.py` |
| M1.05 ledger | `research/sim/ledger.py` |
| M1.06 risk | `research/sim/risk.py` |
| M1.07 analytics | `research/analytics/{metrics,regimes}.py` |
| M1.08 validation + advisory | `research/validation/{splits,walkforward,sensitivity,holdout,compare}.py`, `research/advisory.py` |
| runner/config | `research/runner.py` (frozen `BacktestConfig` → pure `run_backtest` → `RunResult` + checksum) |
| persistence | `db/research_models.py`, `migrations/versions/0002_m1_research.py`, `db/research_store.py` |
| jobs / API | `research_jobs.py`, `api/research.py`; RBAC additions `VIEW_RESEARCH`, `RUN_RESEARCH`, `IMPORT_DATA` |
| tooling | `scripts/fetch_binance_klines.py` (public klines → local files), `scripts/run_research.py` |
| web | `apps/web/src/app/research/{datasets,lab,results,compare}` + e2e |

## Stages (each ends with green ruff/pyright/pytest and a commit)
M1-A data foundation → M1-B simulation foundation → M1-C strategies + risk → M1-D analytics + research → M1-E persistence/API/web/acceptance.

## Acceptance test matrix (required categories → test files under `tests/research/`)
| # | Category | Test module |
|---|---|---|
| 1 | Decimal precision/rounding | `test_rounding.py` |
| 2–4 | import, duplicates/missing, versioning/checksums | `test_data_import.py`, `test_quality.py`, `test_checksums.py` |
| 5–6 | causal indicators, look-ahead prevention | `test_indicators_causal.py`, `test_lookahead.py` |
| 7–8 | grid boundaries, trend-pullback | `test_grid.py`, `test_trend_pullback.py` |
| 9–12 | market fee/slippage, limit conservatism, partial/cancel, min-notional/lot | `test_execution.py` |
| 13–14 | insufficient cash/inventory, no shorts/margin | `test_orders_validation.py` |
| 15–17 | ledger reconcile, realized/unrealized, equity/drawdown | `test_ledger.py`, `test_golden.py`, `test_metrics.py` |
| 18 | daily-loss and drawdown breakers | `test_risk.py` |
| 19 | reproducibility | `test_reproducibility.py` |
| 20 | walk-forward separation | `test_walkforward.py` |
| 21 | API auth/RBAC/idempotency | `tests/integration/test_research_api.py` |
| 22 | migrations up/down + immutability triggers | `tests/integration/test_research_db.py` |
| 23 | browser research workflow | `apps/web/e2e/07-research.spec.ts` |
| 24 | M0 live-lock regression | `tests/live_lock/*` (extended, not weakened) |
| property | balance preservation, non-negative inventory, cash reservation, idempotency, fee calc, reconciliation, reproducible equity | `test_properties.py` (Hypothesis) |
| golden | manually calculated expected outcomes | `test_golden.py` |
