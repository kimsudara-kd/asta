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
