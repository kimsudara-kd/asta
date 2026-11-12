# ASTA M2 — Continuous Paper Trading & Market Observation: Acceptance Evidence

**Directive:** ASTA-M2-IMPLEMENTATION-01 · **Date:** 2026-11-12 · **Baseline:** `m1-accepted-with-residual` at `773b604`

**STATUS: BUILT — NOT ACCEPTED.** The engineering scope is implemented and verified; the 72-hour supervised observation gate was **not** met (1.67 h observed). Nothing was pushed, no M2 tag was created, M3/M4 were not started.

> VIRTUAL FUNDS ONLY. All balances, orders and fills are simulated by the offline engine from public market data; no exchange credential, signer, authenticated connection or order path exists. Nothing here claims economic profitability.

## 1. M1 baseline verification

Verified read-only before any M2 code (2026-11-12): tag `m1-accepted-with-residual` → `773b604` (annotated, local), `m0-accepted-with-residual` → `79f792a`; working tree clean; no remote. `M0-ACCEPTANCE-RECORD.md` and `M1-ACCEPTANCE-RECORD.md` read; residuals R-1..R-3 and R-11..R-15 carried. `ExecutionGateway` default-DENY (`EXECUTION_ENABLED_IN_THIS_BUILD = False`), `Environment` = BACKTEST/PAPER/EXCHANGE_DEMO, egress allow-list with forbidden private/trading/business paths. Host check: 31 GB RAM / 4.8 GB free, 156 GB free on E:; other projects' containers on the machine were left untouched; ASTA used only its own compose databases (:5640 dev / observation, :5641 tests). Plan and dependency map: `docs/architecture/m2-plan.md`. M1 regression guard: after the engine refactor the 189 research tests and the seven full-period real-data checksums are unchanged (re-verified at every engine change).

## 2. Architecture changes and ADRs

* **ADR-0009** reuse of the M1 engine, sub-accounts, global risk, halt semantics, muted steps, physical separation.
* **ADR-0010** PostgreSQL work queue, effectively-once posting, replay recovery, reconciliation, data-interruption policy.
* **ADR-0011** public market-data sources, completeness, the OKX candle decision.
* Engine: `run()` → `begin / settle / decide` (M1 `step()` unchanged in behaviour), optional `PortfolioHooks`, shared `RiskEngine`, `RiskEngine(auto_reset_daily=False)` + `rearm_daily`.

## 3. New module inventory

`src/asta/paper/`: `config` (frozen session config, wallet split, accounts, hash), `states` (state machine), `runtime` (PaperRuntime, SubAccount, hooks, step rows), `store` (effectively-once posting, replay rebuild + verify, reconciliation, transitions), `jobs` (durable queue), `worker` (StepWorker, fault points, input planning), `scheduler` (STEP scheduling, warm-up bootstrap), `collector` (observations, gaps, feed status), `bars` (complete-bucket aggregation), `ops` (incidents/alerts), `reports` (daily CEO report), `views` (API read models). Composition root `src/asta/paper_service.py` (feeds, writer, monitor, clock, backfill, scheduler, worker thread). `src/asta/api/paper.py` (routes), `src/asta/db/paper_models.py`, migration `0004`. Adapters: OKX `candles_paged`, WS URL on 443. Scripts: `paper_observation.py` (create/start/status/measure/gate), `e2e_seed_paper.py`. Web: `/paper` (overview), `/paper/portfolio`, `/paper/orders`, `/paper/risk`, `/paper/performance`, `PaperShell`, `lib/paper.ts`; `usePoll` now keys payloads by path.

## 4. Database migrations

`0004_m2_paper_trading` (down/up tested; models == migrations): market_feed_connections, market_observations, market_data_gaps, paper_sessions, paper_session_configs, paper_session_states, paper_step_inputs, paper_session_checkpoints, strategy_signals, paper_orders, paper_order_events, paper_fills, paper_ledger_entries, paper_balances, paper_equity_snapshots, paper_risk_events, durable_jobs, worker_leases, operational_alerts, daily_paper_reports; the M0 `incidents` table is **reused** (session/venue/symbol/dedupe columns + partial unique index: one OPEN incident per key). Twelve history tables are insert-only (UPDATE/DELETE/TRUNCATE rejected by the existing trigger function); the others are documented mutable projections (session cursor, current order state, balances, jobs, heartbeats, feed status, gap status, alert acknowledgement). Instrument rules are stored in the existing `market_rules_versions` (not duplicated).

## 5. Public exchange capability matrix (verified 2026-11-12 from this machine, public endpoints only)

| Capability | Binance (`data-api/data-stream.binance.vision`) | OKX (`www.okx.com`, `ws.okx.com`) |
|---|---|---|
| Server time (clock offset) | REST `/api/v3/time` — OK (offset about −320 ms) | REST `/api/v5/public/time` — OK (about −326 ms) |
| Instrument rules | REST `exchangeInfo` (BTC/ETH/SOL-USDT) — stored | REST `public/instruments` SPOT — stored |
| 1-minute candles, live | WS `kline_1m`; closed flag `x` | **REST `market/candles` polled every 15 s, `confirm=1` only** (public WS rejects `candle1m`: error 60018; the business WS is deliberately not allow-listed) |
| Trades / quotes | WS `trade`, `bookTicker` | WS `trades`, `tickers` (public endpoint, port 443; 8443 is being retired) |
| History / warm-up / backfill | REST `klines` (≤ 1000) | REST `candles` (≤ 300) + `history-candles` pages (≤ 100) |
| Heartbeat / keep-alive | server pings, client pongs (library) | text ping every 20 s |
| Connection expiry | 24 h rollover ⇒ reconnect + gap check | reconnect + gap check |
| Rate limits | 418/429 ⇒ `RateLimited`, no retry loop | same; 3 calls per 15 s |
| Sequence continuity provable | **No** — only "all minutes present" is asserted per bar | **No** — same |
| Private/trading/user streams | not used, path-denied | not used, path-denied |

## 6. Binance runtime evidence (live public data, 2026-11-12)

Verified at service start and continuously since 16:21 UTC: REST `time` + `exchangeInfo` for BTC/ETH/SOL-USDT stored as rules (source LIVE); WebSocket (`data-stream.binance.vision`) closed 1-minute klines persisted — **101 candles per market over 101 minutes (present fraction 1.000), 9 per market restored from REST backfill after the controlled crash**; clock offset about −320 ms (stored on the feed row); zero reconnect events in the observation window; warm-up of 300 closed hourly/5-minute bars fetched from public REST for both sessions. Evidence: `docs/evidence/m2/observation-measure.json`, `data/paper/service*.log` (local, not committed).

## 7. OKX runtime evidence and limitations

Verified from this machine on 2026-11-12: public REST time + instruments, public WebSocket tickers/trades (470 quotes + 187 trades in a 25 s probe), `candles` and `history-candles` REST (warm-up of 300 bars via paging). Closed 1-minute candles: **102 per market over the same window (present fraction 1.000), 5 per market from REST backfill**; clock offset about −326 ms. **Limitations (unchanged by this evidence):** the public WebSocket cannot serve candles (error 60018; the business endpoint is not allow-listed), so OKX candles are REST-polled every 15 s (up to ~15 s extra latency, bounded by the bar-close grace of 150 s); the WS port moved to 443 after OKX's retirement notice for 8443; official documentation was not re-read in full — behaviour was verified empirically against the live public endpoints; OKX remains an "empirically verified public feed", and R-1 (OKX connectivity/history) is **not closed by this milestone** (no OKX historical research data was imported; the closure decision belongs to the CEO).

## 8. Simulated order and ledger tests

* **Engine reuse proof:** research suite (189 tests) and the seven full-period real-data M1 checksums are unchanged after the engine refactor (`scripts/verify_reproducibility.py`, original checksums matched at each change).
* `tests/paper/test_runtime.py` (23): wallet split exact (6 active accounts sum to 1,000; 500/500 per venue; two shadow benchmarks with separate virtual 1,000), disabled venue keeps its cash idle and in equity, config hash/round-trip/immutability and 8 refused configurations, step-for-step determinism, **every account reconciles with the independent fill-based audit**, global equity identity and no negative cash/inventory/short at every step, rejected signals recorded with reasons and no warm-up noise, other accounts' exposure counts against per-asset (10 %) and total (30 %) caps, stale data blocks entries, muted strategies create no orders, **daily trip does not reset at midnight, re-arm only on a later UTC day, drawdown halt latched**, M1 auto-reset unchanged.
* `tests/paper/test_store.py` (11): 700-step session persisted and reconciled from durable rows only; redelivered step rejected with nothing written; failure mid-posting rolls the whole step back (cursor unmoved); **rebuilt runtime continues exactly like the original**; restart with open paper orders recovers them; replay detects tampered inputs / missing checkpoint / wrong cursor; reconcile flags corrupted ledger, projection and fills; history tables append-only (UPDATE/DELETE/TRUNCATE refused); state machine enforced and logged; config tamper detected.
* Duplicate signals/orders: engine `client_id` idempotency (M1 tests) + `(session, account, signal_id)` / `(session, account, order_id)` uniqueness.

## 9. Risk and halt evidence

Unit: per-asset and total caps including other accounts' exposure (`test_other_accounts_exposure_counts_against_the_per_asset_cap`), shared single `RiskEngine`, daily/drawdown latching and re-arm rules, risk state restored by replay (checkpoint digests include peak equity and trip flags). API: re-arm refused for a latched drawdown halt and for a same-UTC-day daily trip; resume refused with open critical incidents, failed reconciliation or stale data. Worker (tested with deliberately tight limits): a risk trip moves the session RUNNING → RISK_HALTED, persists the risk events, opens DAILY_LOSS_LIMIT / MAX_DRAWDOWN incidents, and bars keep being absorbed while halted. **No live halt occurred during the supervised observation** (equity stayed within limits), so halt behaviour is evidenced by tests and fixtures only.

## 10. Durable worker and recovery results

`tests/paper/test_worker.py` (20, all deterministic): happy path equals the in-memory reference digest-for-digest; idempotent job creation and redelivery no-op; **abrupt death at each of five fault points (before handle, before step, after step before persist, after persist, before commit) → new worker with an empty cache reaches the identical state and posts no duplicate fills**; three concurrent workers process 380 steps exactly once, in order; transient failures back off and dead-letter with a CRITICAL incident; permanent failures not retried; poison job with spent retries dead-lettered; lease heartbeat/ownership/expiry; cancelled session work dropped; **reconciliation mismatch ⇒ session FAILED (fail-closed) + incident**; **replay mismatch on restart ⇒ job DEAD, session FAILED, REPLAY_MISMATCH incident**; late steps muted; DB-outage-style transient error retried with nothing half-posted; graceful drain then resume; restart logged as RUNNING → RECOVERING → RUNNING.

## 11. Controlled fault-injection results

| Fault | Where | Result |
|---|---|---|
| Process crash at 5 distinct points | worker tests (deterministic) | identical state after restart, no duplicates |
| Two/three concurrent workers | worker test | each job once, strict order |
| Worker lease expiry / crashed owner | worker + queue tests | job reclaimed, attempts counted |
| DB interruption (transient error after persist) | worker test | rolled back, retried with backoff, correct final state |
| Tampered step input / deleted checkpoint / wrong cursor | store + worker tests | replay refuses; session FAILED |
| Tampered ledger / projection / fill | store + worker tests | reconcile flags; session FAILED |
| Stale / disabled / unknown feed | feed tests | entries blocked (`STALE_DATA`), fail-closed |
| Missing candles | feed + service tests | gap opened → backfilled from REST or UNRECOVERABLE; bar withheld |
| Duplicate / out-of-order candle | feed tests | ignored+counted / fills the hole and closes the gap |
| WebSocket server closes connection | service test (fake adapter) | stream re-opened, missed minutes backfilled, contiguous afterwards |
| Silent feed | service test | RECONNECTING + FEED_DISCONNECTED incident |
| Unverifiable venue (network block) | service test | DISABLED with stored reason, no stream, no bypass |
| HTTP 418/429, redirects | contract tests | `RateLimited`, no retry loop; redirect never followed |
| Outage catch-up (steps hours late) | worker test | bars absorbed, strategies muted, no order from a stale decision |
| **Live: hard kill of the running service (16:36:19 UTC), restart after 9 minutes** | supervised observation | see §14: gaps detected (8–9 min per market) and all six backfilled from public REST; 5m session runtime restored by **verified replay of 303 steps**; steps resumed; reconciliation OK |

## 12. Full regression counts (HEAD at the evidence commit)

* **Python:** `ASTA_RUN_NETWORK_TESTS=1` → **451 passed, 0 failed, 0 skipped**; default → **447 passed, 4 skipped** (the 4 are the network opt-ins). By area: research 189, integration 90, **paper 84** (runtime 23, store 11, worker 20, feed 15, API/reports 10, service 5), unit 49, contracts 24 (M2 added 4), live_lock 15 (M2 added 8). M1 closed at 355; M2 adds 96 (the M0/M1 tests were not modified except the explicit route allow-list enumeration).
* **Browser (Playwright, system Chrome):** live 16 + nodata 1 + aging 1 + research 12 + **paper 9 = 39/39** (`docs/evidence/m2/e2e-full-run.log`).
* **Web:** unit 7/7, `tsc` clean, `next build` OK.
* **Static:** ruff + format clean; pyright 0 errors; mypy strict 142 files clean (no new exclusions); pip-audit / pnpm audit clean; secret scan 0; gitleaks 37 commits clean.

## 13. Browser screenshots

`docs/evidence/m2/screenshots/` (all captured from the final build; the frozen M0/M1 screenshot folders are byte-identical to the M1 tag): `20-paper-overview`, `21-paper-portfolio`, `22-paper-signals-orders`, `23-paper-risk`, `24-paper-performance` (equity vs both benchmarks, drawdown, strategy/fee table, daily report), `25-paper-stopped`, `26-paper-mobile`, plus `01–19` (M0/M1 pages re-captured). **The paper pages in the browser tests show a SYNTHETIC fixture session** (labelled "e2e fixture session"; its reports say `NO_STORED_OBSERVATIONS (fixture/test input)`); they demonstrate the UI, not market connectivity.

## 14. Operational uptime and data-gap measurements (supervised observation — **72-hour gate NOT met**)

Environment: ASTA's own compose PostgreSQL (:5640) and the `paper_service` process on this Windows PC, real public Binance + OKX data, two sessions of 1,000 virtual USDT (500/500, 3 strategies, 6 active accounts + 2 shadow benchmarks): `observe-1h` (warm-up 300 hourly bars) and `observe-5m` (same strategies/parameters on 5-minute bars, used to exercise the step/recovery machinery 12× more often; M1 results exist only for the 1 h timeframe, so this is an engineering-stability session, not a research claim). Started 16:24 UTC; measured at 18:03 UTC.

| Measure | Result |
|---|---|
| Elapsed supervised time | **1.67 h of the 72 h required** — gate evaluator (`scripts/paper_observation.py gate`) returns `duration_met: false`, `gate_pass: false` |
| Live steps processed | `observe-5m` 20 (19 expected + 1 catch-up); `observe-1h` 2 (the first hour began mid-hour at service start, so its bar was correctly withheld as DATA_GAP — nothing is back-filled into a bar) |
| Steps with blocked markets | 5m: 3 (service start, the post-crash bar whose minutes were still being backfilled, and one Binance SOL minute); 1h: 1 |
| Muted / late steps | 0 |
| 1-minute candle presence per market | **1.000** over the window (Binance 101/101, OKX 102/102) |
| Data gaps | 6 opened (3 Binance + 3 OKX) by the live crash test; **all 6 BACKFILLED from public REST**; 0 UNRECOVERABLE, 0 OPEN |
| Reconnections | 0 (both feeds) |
| Reconciliation discrepancies | **0** (both sessions; `last_reconcile_ok = true`) |
| Duplicate fill postings | 0 |
| Queue | 640 jobs DONE, 0 pending, **0 dead**, backlog 0 |
| Service starts | 4: initial start, **restart after a hard kill with 9 min downtime (live crash test)**, and two planned restarts to load hardened code |
| Live crash recovery | killed 16:36:19, restarted 16:45:09: gaps detected and repaired; `observe-5m` runtime restored by **verified replay of 303 steps** (RECOVERY incident, session state RUNNING → RECOVERING → RUNNING in later builds); steps resumed with continuous `step_seq` |
| Live DB interruption | ASTA's dev PostgreSQL container stopped 17:05:17–17:06:33 (76 s): every supervision loop logged the error and survived; no candle lost (continuous 17:00–17:08); queue and sessions continued afterwards |
| Service footprint | ~125 MB working set |
| Real orders / authenticated calls | none possible (see §16); outbound requests in the logs are only GETs of public endpoints |
| Simulated outcome at measurement | `observe-5m` equity 1,000.296 USDT (2 fills), `observe-1h` 1,000.000 (no fills) — **meaningless as performance; 1.7 hours** |

**What this does and does not show.** It shows the data path, queue, replay recovery, reconciliation and fault handling working on live public data and through a hard kill and a DB outage. It does **not** show 24/7 reliability: 72 uninterrupted hours (the acceptance gate) were not run, and I will not declare reliability without them. Recommended next step: leave `python -m asta.paper_service` running supervised for ≥ 72 h (service, sessions and the gate command are in place) and attach the `measure`/`gate` output.

## 15. Daily CEO performance reports

The report builder (`asta.paper.reports`) produces one immutable report per **completed** UTC day; it is reproducible (`verify_report` recomputes it from durable rows and compares the hash), refuses days that are incomplete or sessions that fail reconciliation, and has no generated commentary (`ai_commentary: null`; one deterministic sentence). Sections: financial (opening/closing equity, net/realised/unrealised movement, fees, estimated slippage, return, drawdown, cash, inventory, venue contribution), trading (signals, rejections, orders, fills, partials, cancels, win rate / average gain/loss with N/A when not valid, strategy P&L, **both** passive benchmarks and all-cash), operations (uptime, feed availability, gaps, restarts/recoveries, reconciliation failures, risk incidents, unresolved alerts) and research conclusion (profitable vs not, risk-compliant vs breaching, `STORED_PUBLIC_OBSERVATIONS` vs `NO_STORED_OBSERVATIONS (fixture/test input)`, observed vs hypothetical, explicit "not evidence of a profitable edge"). Tests cover reproducibility, immutability, tamper detection, balance identities (net = realised + unrealised movement; venue contributions add up) and the API gates; the browser test creates and opens a report from the UI. **No completed UTC day of live observation exists yet** (the run began 16:24 UTC on 2026-11-12), so no live daily report was issued; `docs/evidence/m2/observation-measure.json` contains a clearly flagged **partial-window preview** (`window.partial: true`) for each live session, which is not a CEO report.

## 16. Security and live-lock verification

* **Physical separation (AST tests, `tests/live_lock/test_paper_boundaries.py`, 8 tests):** `asta.paper` imports no exchange adapter and none of httpx / websockets / aiohttp / requests / urllib.request / http.client / socket / ssl / subprocess / ftplib / smtplib / grpc; `asta.exchanges` never imports `asta.paper`; `asta.api.paper` imports no adapter; **no credential/signing vocabulary** (hmac, api_key, secret_key, passphrase, signature, listenKey, …) in any identifier or short string constant of any M2 module; `paper_service` imports only the two existing public adapters plus their shared base/ws helpers.
* **Gateway / environment / egress:** `EXECUTION_ENABLED_IN_THIS_BUILD = False`, `LIVE_TRADING_ENABLED = False`, `Environment` = {BACKTEST, PAPER, EXCHANGE_DEMO}, `ExecutionGateway.authorize()` still DENY for a PAPER request; `git diff 773b604 -- src/asta/risk src/asta/core/egress.py src/asta/core/environment.py` is **empty** (not one line changed). The egress allow-list was not loosened: private/trading/business paths (`/ws/v5/business`, `/api/v5/trade`, `/api/v3/order`, `userDataStream`, …), non-TLS, other hosts, credentials-in-URL remain denied (contract test); redirects are never followed.
* **API:** route-table live-lock test extended by an *explicit enumerated* paper allow-list (`/v1/paper/sessions/{sid}/orders` is the only path containing a banned word; it lists SIMULATED orders; every mutating paper route is enumerated and acts on local simulated state only; still no PUT/PATCH/DELETE route anywhere). RBAC verified directly against the API (viewer/reviewer 403 for every control route, 401 unauthenticated), CSRF and mandatory `Idempotency-Key`, audit events for every state change (`paper.session.*`, `paper.alert.ack`, `paper.report.create`); `Permission.VIEW_PAPER` / `CONTROL_PAPER` added to the static default-deny map.
* **UI:** every paper page shows `PAPER TRADING — VIRTUAL FUNDS ONLY`; browser test scans all paper pages' controls for execution vocabulary (none); viewers get no create/control buttons and the server independently returns 403.
* **Static checks:** ruff clean; pyright 0 errors; **mypy strict clean on 142 files**; `pip-audit` and `pnpm audit` no known vulnerabilities; custom secret scan 0 findings; gitleaks over all 37 commits and the tree: no leaks. No credentials exist anywhere; settings still refuse exchange-credential environment variables.
* **Not closed:** R-3 — infrastructure-level egress enforcement (deployment firewall/allow-list) is still absent; the egress control remains application-level. M2 introduces no credential, so the gate is not triggered, and it is **not** claimed closed.

## 17. Local commits and unresolved defects

Commits since `773b604` (all local, nothing pushed): `38a50b4` (engine increments + plan), `02638e7` (M2-A..D backend), `e121c4b` (frontend, e2e paper phase), `58b0300` (ADRs, service tests, recovery state), `58b0300`/`08229b4`/`3de521c` (recovery state, hardening, evidence routing) and `bf2cda0` (risk-halt test + this document); a final documentation-only commit may follow — the closing report gives the exact final hash. Tags `m0-accepted-with-residual` / `m1-accepted-with-residual` untouched; **no M2 tag was created**.
**Defects found and fixed during M2 (all with regression tests):** muted strategies starved of bars (indicator state desync) → strategies are now always consulted, orders rejected as WARMUP when muted; warm-up ledger entries not persisted (initial cash) → money rows always persisted; balance projection was self-healing so tampering the projection went unnoticed → reconciliation compares ledger sums, fills and projection, and tamper tests corrupt the ledger; scheduler grace shorter than backfill latency (the 16:35 5-minute bar after the live crash test was processed as DATA_GAP although its minutes were backfilled seconds later) → grace raised to 150 s; stale poll data crashed pages on path change → `usePoll` keys payloads by path; DB-outage fragility of two supervision loops → hardened and re-tested live.
**Open / known:** see §18 and §14 (the 72-hour gate is not met).

## 18. Residual-risk register (current)

| ID | Residual | Status |
|---|---|---|
| **M2-G1** | **72-hour continuous-observation gate not executed** (1.67 h measured); 24/7 reliability unproven | OPEN — blocks acceptance |
| **R-1** | OKX connectivity/history: public REST + WS verified live; candles only via REST polling (public WS lacks them); no OKX history imported; docs not exhaustively re-read | OPEN — CEO decision |
| R-2 | Hosted CI never executed (no remote) | OPEN |
| **R-3** | Infrastructure-level egress policy absent (application-level only) — **mandatory before any credentials**; M2 adds none | OPEN — MANDATORY GATE |
| R-11 | Fee schedules and trading rules assumed (rules are now current public snapshots, fees still assumed; real fills would differ) | OPEN |
| R-12 | Queue is PostgreSQL-only; single worker process model; no multi-host failover | by design |
| R-13 | Studies/observation sessions are started by operator tooling or the UI; strategies parameters are M1 defaults | OPEN |
| R-14 | Observation on one Windows PC: host sleep/power loss ends the service; recovery is by restart + replay (tested), not automatic supervision | OPEN |
| R-15 | M1 risk-limit calibration finding stands (limits strict for volatile assets) | OPEN |
| M2-1 | Candle-only fill model: no order-book/queue information; spread snapshots are context, not used for fills | OPEN |
| M2-2 | A bar whose minutes arrive after the 150 s scheduler grace is processed as DATA_GAP and never reprocessed (deliberate: nothing is reconstructed later) — can leave short blind spots after outages | accepted |
| M2-3 | Strategy parameters tuned for 1 h bars are also run on 5 m bars in the stability session | accepted (labelled) |
| M2-4 | Replay recovery cost grows linearly with session length (303 steps ≈ seconds); checkpoint-based fast restore not implemented | OPEN |
| M2-5 | No external notification channel (in-app alerts only, by directive) | by design |
| R-4…R-10 | M0 residuals | carried |

## 19. Economic observations

None of this is a profitability result. M1 found no validated edge and all strategies are marked `deployment_candidate: false`. The supervised sessions produced 2 simulated fills in 1.7 hours (equity +0.30 USDT on 1,000, inside noise and fee/slippage assumptions); most signals were rejected by the 10 %-per-asset cap (each strategy slice holds ~167 USDT against a 100 USDT global per-asset cap) or by data/warm-up rules, which the Signals page shows with reason codes. The benchmark lines (constrained and unconstrained passive hold, all-cash) are reported next to the portfolio so any future outcome is read against them. A longer forward period is needed before any claim, and none is made here.

## 20. M2 acceptance recommendation

**Status: BUILT — NOT ACCEPTED.** All M2 engineering scope is implemented and verified (451/451 Python tests with network opt-ins, 39/39 browser tests, strict static checks, live-lock/security regression, replay recovery, fault-injection suite, live hard-kill and DB-outage tests, UI, API, reports), but the directive's acceptance gate — **72 consecutive supervised hours** — has not been run: only 1.67 hours were observed. Acceptance cannot honestly be recommended until that run completes with no reconciliation discrepancy, no duplicate posting, no silent gap and no unauthorised transition (the tooling to measure each of these exists). Everything else is ready for CEO review; no M2 acceptance tag was created and none should be until the gate passes. M3/M4 were not started; no exchange credential, signing code or order path exists.
