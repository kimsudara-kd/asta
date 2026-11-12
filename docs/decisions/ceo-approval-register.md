# CEO approval register

## Design approvals (handoff §22)
Approved for design: 2 exchanges; 24/7 observation; spot only; future pilot cap USD 1,000; backtesting first, paper second, exchange demo third; no live trading.

## ASTA-M0-IMPLEMENTATION-01 / ASTA-M0-R1
M0 implementation and remediation authorised (demo-only scope, no live trading, no push).

## ASTA-DECISION-003 — M0 acceptance closure (2026-11-12)
- **Decision:** carry unresolved OKX public-network verification as a documented M0 residual (R-1); authorise formal M0 closure as `ACCEPTED_WITH_RESIDUAL`; authorise M1 (Offline Historical Backtesting Engine) effective after closure, implementation only after a separate handoff.
- **Preserved as open:** R-1 OKX network dependency (external), R-2 hosted GitHub CI not executed, R-3 infrastructure egress enforcement = **mandatory gate before any exchange credentials**.
- **Not authorised:** authenticated exchange access, order submission, paper-trading service, real-money trading, GitHub push.
- **Record:** `docs/evidence/M0-ACCEPTANCE-RECORD.md` · tag `m0-accepted-with-residual` (local).

## ASTA-DECISION-004 — M1 engineering acceptance (2026-11-12)
- **Decision:** approve M1 (Offline Historical Backtesting & Strategy Research Engine) as `ACCEPTED_WITH_RESIDUAL`, subject to final evidence verification (done at 7a9100d) and local closure.
- **Basis:** data/validation engine, simulation + ledger, strategy + risk engines, research validation/comparison, 16 persisted studies with provenance and de-duplication, 7/7 full-period configurations reproduced exactly, type-check exclusions removed, Python/browser/security/static checks passed. **No validated profitable trading edge was demonstrated; economic profitability is NOT established.**
- **Carry-forward residuals:** R-11 unverified fee/rule assumptions; R-1 OKX connectivity and historical data limits (REST contract test observed passing at closure; WS/history unverified; CEO decision pending); R-2 no hosted CI; R-12 no durable queue/resume; R-13 studies script-generated; R-14 one-platform reproducibility; R-15 risk limits constrain passive benchmarks; R-3 infrastructure egress policy absent (mandatory before any credentials).
- **Not authorised:** M2, continuous paper trading, exchange demo orders, authenticated exchange connections, real trading, margin/futures/leverage, exchange credentials, GitHub push or remote creation. Default-deny gateway unchanged.
- **Record:** `docs/evidence/M1-ACCEPTANCE-RECORD.md` · tag `m1-accepted-with-residual` (local, annotated).
