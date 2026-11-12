# ADR-0007: Ledger, cost basis and risk-limit definitions

Status: accepted (M1)

**Ledger.** Immutable, sequence-numbered entries (`INITIAL_CASH`, `RESERVE`, `RELEASE`, `FILL_BASE`, `FILL_QUOTE`, `FEE`, `CORRECTION`). Balances are a pure function of the entries; errors are fixed with `CORRECTION` entries, never by editing. Quantum 1e-8 for all amounts: buy notional rounds **up**, sell notional **down**, fees **up**, order quantity **down** to the lot step. Equity = cash + Σ qty × mark. A `reconcile()` check recomputes balances from entries and verifies `equity − initial = realized + unrealized` exactly (Decimal, no tolerance).

**Cost basis: weighted average** (per asset, in quote). Chosen over FIFO because the strategies scale in/out of one position and the engine has no tax-lot requirement; documented and applied consistently. Cost relieved on a sell = `quantize(cost_total × qty_sold / qty_held)`; `realized = net proceeds − relieved cost`. Buy fees charged in base reduce received quantity (raising unit cost); sell fees in quote reduce proceeds.

**Risk limits — each measured against an explicit base:**
| Limit | Default | Base |
|---|---|---|
| Max exposure per asset | 10 % | **starting equity** (100 USDT of 1,000); exposure = mark-to-market inventory value + notional of open buy orders |
| Max total exposure | 30 % | **starting equity** (300 USDT), same exposure definition |
| Daily loss breaker | 2 % | **UTC-session-start equity**; loss includes unrealized P&L; trips new risk-increasing orders until the next UTC day |
| Cumulative drawdown halt | 5 % | **peak equity** (running maximum of mark-to-market equity); hard halt for the rest of the run |
Exposure caps are checked at order validation and again at activation. Risk-increasing = any buy; sells/protective exits are never blocked. A halted run is reported `STOPPED_RISK_HALT` and counted as **failed**, whatever its P&L. Limits are persisted with the run and are immutable during it. No leverage, margin, shorting or negative cash exists in the engine.

**Baseline fairness.** Baselines run through the same engine and cost model. Risk-compliant baselines use the same limits; "reference" baselines that deliberately exceed the caps (e.g. full-capital hold) are labelled `RISK_UNCONSTRAINED_REFERENCE`.

**Metrics honesty.** Undefined or statistically invalid metrics are `None` with an `na_reason` (e.g. Sharpe needs ≥ 30 daily observations and non-zero variance; annualised return needs ≥ 90 days; profit factor with no losing trade is N/A). Sharpe/Sortino: daily equity returns, risk-free 0, √365 annualisation (24/7 market), MAR 0.
