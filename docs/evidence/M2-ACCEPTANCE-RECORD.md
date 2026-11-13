# M2 acceptance record

**Final M2 status: `ACCEPTED_WITH_RESIDUAL`**
Authority: ASTA-DECISION-006 (CEO), 2026-11-13, closing M2 under the revised acceptance policy of ASTA-DECISION-005 · Candidate commit reviewed: `a99eb3c`
Evidence reviewed: `docs/evidence/M2-acceptance.md` (M2 sections 1-20, addenda R1, R2, R3, R4/R4A, the interim completion report and the ASTA-DECISION-005 section), `docs/evidence/m2/*`
Local tag: `m2-accepted-with-residual` (annotated, local only; **not pushed**; no remote is configured). The tag points at the commit that contains this record.

Earlier status lines inside `M2-acceptance.md` ("BUILT - NOT ACCEPTED", "ACCEPTANCE CANDIDATE") are history and are preserved unchanged; this record supersedes them for status only. No recorded measurement, gate output, incident, test count or checksum was altered. The M0 and M1 acceptance records and their annotated tags (`m0-accepted-with-residual` -> `79f792a`, `m1-accepted-with-residual` -> `773b604`) are untouched.

## 1. Engineering acceptance is not economic profitability
M2 accepts the **continuous paper-trading and market-observation platform** (durable queue, effectively-once ledger posting, deterministic replay recovery, venue isolation, risk enforcement, reporting, supervision tooling). It does **not** establish that any strategy is profitable, and it does **not** establish sustained 24/7 reliability.
* **Profitability not demonstrated.** The five-minute session ended at equity 998.985999 on 1,000 virtual USDT (net -1.014001; all of it from `sma_cross`); the one-hour session made no trade (equity 1,000.00). Over about 15 hours these are meaningless as performance evidence and carry no edge claim.
* **Sustained 24/7 reliability not demonstrated.** See section 3.

## 2. Original acceptance criteria and the CEO timing-gate waiver
Original M2 criteria (ASTA-M2-IMPLEMENTATION-01 and the R1-R4 remediation directives) included a **72-hour genuine supervised observation** with **supervisor coverage >= 95 %** (the `paper_observation.py gate` thresholds, unchanged), plus the financial, security and regression gates below.

**ASTA-DECISION-005 (CEO acceptance-policy exception)** removed the 72-hour minimum and the 95 % coverage threshold as *mandatory* conditions for engineering acceptance. They are recorded as **NOT PASSED - WAIVED**. They were not achieved and are not claimed. The gate script and its failed/immature outputs (`r1`..`r4a`, `final-*`, `d005-*` gate JSON) are preserved unchanged. Register entries: `docs/decisions/ceo-approval-register.md`.

## 3. Actual measured observation (not waived away)
| Window | Elapsed | Supervisor coverage | Service availability |
|---|---|---|---|
| From the approved start `2026-11-12T20:11:22Z` | 11.29 h of the original 72 h | 83.9 % | 83.5 % |
| From the session start (16:23 UTC) | 15.09 h / 15.07 h | 62.8 % / 62.9 % | 62.5 % |

`gate_pass: false` in both windows for duration and coverage only. Contributing events (all recorded, none hidden): 3.8 h before a supervisor existed; a 66-minute PC suspension (gap-detected and fully backfilled); several planned restarts and kill tests; the Task Scheduler launch defect (priority 7 starvation, fixed in R4A); one unexplained `0xC000013A` relaunch failure. Sessions `afec3e2b-c5dc-4306-a98d-82da6ea38346` (hash `dd59484f...febefb84`) and `489d0990-6cf9-4120-9ad7-c6d370374dc2` (hash `f06aaf1a...2eae72`) were never recreated or reset and continue to run for research.

## 4. Binance and OKX operational limitations
* **Binance:** connected, fresh public data throughout (public REST/WS only); outages only at restarts/tests and the PC suspension. Step data coverage 159/181 complete-and-fresh (five-minute session).
* **OKX:** **blocked continuously since 2026-11-13 03:00:16 UTC** by the upstream DNS sinkhole (residual R-1); not bypassed. Feed DISABLED with a stored reason and a `VENUE_DISABLED` incident; 113/181 steps complete-and-fresh; no OKX order accepted after the outage, cash/inventory/ledger preserved. **OKX continuous public connectivity is unverified**; OKX candles come from REST polling (public WS rejects `candle1m`).

## 5. Final financial and operational results (`docs/evidence/m2/final-report.json`)
| | observe-1h | observe-5m |
|---|---|---|
| Equity / net P&L | 1,000.000000 / 0.00 | 998.985999 / -1.014001 (realized -0.885792, unrealized -0.128209) |
| Orders / fills | 0 / 0 | 244 (9 filled, 5 cancelled, 230 rejected: 189 asset-cap, 41 insufficient cash) / 9, notional 809.398 |
| Fees (assumed) / est. slippage | 0 / 0 | 0.809399 / 0.473120 |
| Max drawdown | 0 % | 0.197 % |
| Negative balance rows / risk halts | 0 / 0 | 0 / 0 |
| Reconciliation / duplicates | 0 discrepancies / 0 | 0 discrepancies / 0 |
| Checkpoints | all 315 verified by replay | all 481 verified by replay |

Operational: 0 unresolved critical incidents, 0 dead-letter jobs, database at migration head `0005`, daily reports (latest revision) reproducible, audited supersede/invalidate records for the defective first-attempt and pre-session reports, 7,714 outbound GET requests and no other HTTP method in the service log, `exchange_connections` empty. All figures are simulated, assumed-fee, estimated-fill values on virtual funds.

## 6. Mandatory gates (non-waived) - verification at closure
| Gate | Verdict |
|---|---|
| No unexplained reconciliation discrepancy | MET (0; live re-check at closure, both sessions) |
| No duplicate financial posting | MET (0) |
| No negative virtual balance from an accounting defect | MET (0 rows) |
| No unauthorised risk increase | MET (cap/cash refusals live; disabled-venue isolation tested and observed) |
| No unresolved critical incident | MET (0, re-checked at closure) |
| No unverified or fabricated financial report | MET (see section 5) |
| Regression and security checks | MET (section 7) |
| M0/M1 safeguards intact | MET (`git diff 773b604` over risk, egress, environment and all M0/M1 evidence empty; 62 passed / 1 skipped in the live-lock + contracts + runtime re-run at closure) |
| No exchange credentials or authenticated order path | MET |
| 72-hour observation; 95 % supervisor coverage | **NOT PASSED - WAIVED** (ASTA-DECISION-005) |

## 7. Test results and skipped checks (at `a99eb3c`)
Python with network opt-ins **465 passed, 1 skipped, 0 failed** (the skip is the OKX live probe, blocked by the DNS sinkhole; it is **not** a pass); browser **39/39**; ruff check/format clean; mypy strict no issues (144 files); pyright `src` 0 errors; pip-audit and pnpm audit no known vulnerabilities; custom secret scan 0; gitleaks no leaks (48 commits). Not run: hosted CI (R-2); the OKX live probe.

## 8. Residual risks (carried forward, not closed)
**Security:** R-3 infrastructure-level egress enforcement not implemented (application-level default-deny only); credential storage/isolation not built (none exists). **Reliability:** sustained 24/7 operation unverified; dependence on the PC staying awake and the user logged on; unexplained `0xC000013A`; one-interval keep-alive loss after a forced supervisor kill. **Data/market:** OKX continuous public connectivity (R-1); fee and trading-rule assumptions unverified (R-11); fills are estimates. **Process:** hosted CI not executed (R-2). **Typing:** 29 noncritical pyright findings outside `src` (test fixtures, optional narrowing, private-attribute test access, possibly-unbound loop variable, two developer scripts); classified in `M2-acceptance.md` D5-6. Earlier items M2-1..M2-5.

## 9. Conditions for progressing to M3 and mandatory carry-forward gates
M3 is **not started and not authorised by this record**. Before **any authenticated exchange demo integration**: (1) implement infrastructure-level egress restrictions (R-3); (2) verify credential storage and isolation; (3) complete an independent security review; (4) verify the relevant exchange demo API capabilities; (5) obtain explicit CEO authorization. No real-money trading, margin, futures or leverage is authorised.

## 10. Restrictions in force
No exchange credentials exist or were created; no authenticated or demo order path was enabled; no real trading; no change to strategies, risk limits or fee assumptions; no push (no remote); no M3 work; historical M2 evidence and the original paper sessions untouched for closure.
