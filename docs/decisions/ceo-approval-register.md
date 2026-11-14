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

## ASTA-DECISION-005 — Remove the M2 timing gate (2026-11-13)
- **Decision (documented CEO acceptance-policy exception):** the 72-hour minimum observation duration and the associated 95 % supervisor-coverage threshold are removed as **mandatory** conditions for M2 *engineering* acceptance. The original thresholds were **not achieved** and must never be reported as achieved; the failed/immature gate results (`docs/evidence/m2/r*-gate-*.json`, `final-gate-*.json`, `d005-gate-*.json`) are preserved unchanged.
- **Prospective criteria:** `docs/evidence/M2-acceptance.md` (section "ASTA-DECISION-005") lists the revised mandatory gates (reconciliation, duplicates, balances, risk, incidents, reports, regression/security, M0/M1 safeguards, no credentials). The `paper_observation.py gate` thresholds were **not edited**; the waiver is a policy decision recorded here.
- **Retained residuals:** unverified 24/7 long-duration reliability; OKX continuous public connectivity (R-1); infrastructure egress enforcement (R-3, mandatory before any credentials); unverified fee/trading-rule assumptions (R-11); hosted CI not executed (R-2); noncritical operational/typing items.
- **Not authorised:** M2 acceptance tag (separate approval), M3, demo/real orders, credentials, GitHub push.

## ASTA-DECISION-006 — M2 formal acceptance closure (2026-11-13)
- **Decision:** approve M2 (Continuous Paper Trading & Market Observation) as `ACCEPTED_WITH_RESIDUAL` under the revised policy of ASTA-DECISION-005, on the evidence at commit `a99eb3c`.
- **Original 72-hour observation gate and 95 % supervisor-coverage gate: NOT PASSED - WAIVED** (measured: 11.29 h / 83.9 % in the approved window; 15.1 h / 62.8 % from session start). Not claimed as achieved.
- **Not demonstrated:** profitability of any strategy; sustained 24/7 reliability. Engineering acceptance only.
- **Carry-forward residuals:** R-3 infrastructure egress enforcement (mandatory before any credential); R-1 OKX continuous public connectivity (blocked since 2026-11-13 03:00:16 UTC); R-11 fee/trading-rule assumptions; R-2 hosted CI not executed; unexplained `0xC000013A` relaunch; 29 noncritical pyright findings in tests/scripts; M2-1..M2-5.
- **Mandatory gates before any authenticated exchange demo integration:** infrastructure egress restrictions; credential storage/isolation verification; independent security review; exchange demo API capability verification; explicit CEO authorization.
- **Not authorised:** M3 implementation, credentials, authenticated exchange operations, demo or real orders, real-money trading, GitHub push.
- **Record:** `docs/evidence/M2-ACCEPTANCE-RECORD.md` · tag `m2-accepted-with-residual` (local, annotated).

## ASTA-DECISION-007 — M3-B offline engineering ratification and security remediation (2026-11-14)
- **Decision:** retain the existing offline M3-B implementation and its committed history (`c95b826`..`924da22`) as an engineering baseline; remediate offline only. **Status BUILT — NOT ACCEPTED; credential readiness NO-GO.** This is not M3-B acceptance and not authorisation of authenticated exchange integration.
- **Conditionally retained:** the earlier paper-engine changes (opt-in repeat guard default OFF, `order_identity.py`), subject to the regression and accounting verification recorded in `docs/evidence/M3B-DECISION-007.md`.
- **Preserved, history not erased — governance deviations D-1..D-7** (`docs/evidence/M3B-GOVERNANCE-01.md` s.2): D-1 M3-B preparatory code built and pushed on chat instructions without a written directive; D-2 pushes to `main` under the owner's standing instruction; D-3 paper-engine changes; D-4 additive migrations applied to the dev database; D-5 synthetic research data in the dev database; D-6 evidence omitted skipped tests; D-7 no capability beyond offline scope. Closure status of each is in the evidence file.
- **Recorded repository facts:** approved repository `https://github.com/kimsudara-kd/asta` (owner account `kimsudara-kd`); historical pushes of `993225d`, `4514de3`, `924da22` were made with the `gh` credential of `kimsudara-kd` by overriding git's default `osxkeychain` helper, whose stored account `kimsudara` has no write permission (HTTP 403). Earlier pushes up to `2200313` succeeded through the then-configured credentials. Commit authors: owner (to `fb7770b`), `claude <noreply@anthropic.com>`, `Apple <apple@host.local>`.
- **Not authorised (unchanged):** exchange credentials, authenticated or demo orders, live execution, external order signing, relaxation of live-lock controls, margin/futures/leverage/transfers/withdrawals, M3-C, M4, GitHub push, unapproved host provisioning or privileged changes. No acceptance tag.
