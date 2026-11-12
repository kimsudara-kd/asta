# M2 plan — Continuous Paper Trading & Market Observation (ASTA-M2-IMPLEMENTATION-01)

Status: working plan written after the read-only baseline audit (2026-11-12). Decisions are recorded in ADR-0009..0011.

## 1. Baseline audit (read-only, before any M2 code)

| Check | Result |
|---|---|
| M1 tag / commit | `m1-accepted-with-residual` → `773b604` (annotated, local); `m0-accepted-with-residual` → `79f792a`; working tree clean; no remote |
| Acceptance records | `M0-ACCEPTANCE-RECORD.md`, `M1-ACCEPTANCE-RECORD.md` read; residual register R-1..R-3, R-11..R-15 carried (R-3 infrastructure egress = **mandatory before any credentials**; M2 does not add credentials) |
| Gateway | `EXECUTION_ENABLED_IN_THIS_BUILD = False`; `Environment` = BACKTEST / PAPER / EXCHANGE_DEMO (no LIVE); egress allowlist hard-coded with a forbidden-path list |
| Existing market data (M0) | `exchanges/binance`, `exchanges/okx` public adapters (REST + WS: trade, bookTicker, kline_1m), `exchanges/common/{http,ws}` (egress-checked client, equal-jitter backoff), `market_data/{state,service,fixture_feed}` (in-memory dedupe, freshness, per-venue supervisor with clock-drift check) — **no persistence, no backfill, no candle continuity checks, no durable work** |
| M1 simulation contracts | `research/sim/{ledger,execution,orders,risk,engine}`: Decimal ledger with immutable entries, conservative candle-only fill model, risk engine, strategies on closed bars. `Engine.run()` was a whole-run loop (no incremental API) |
| Host | 31 GB RAM / 4.8 GB free at audit, 156 GB free on E:; other projects' containers (`pethhub-postgres`, `bda-proxy`) are running and are **not touched**; ASTA uses only its own compose test DB (:5641) and, for M2, the dev DB profile (:5640) |
| Live OKX state at audit | `www.okx.com` resolves; OKX public REST works; OKX public WS (`wss://ws.okx.com:8443/ws/v5/public`) delivered 470 quotes + 187 trades in 25 s. Recorded as evidence; R-1 closure is a CEO decision |

## 2. Reuse, not duplication

* **One ledger / fill model / risk engine.** The paper runtime drives the *same* `Engine` as M1 through a new incremental interface (`begin / step / finish`; `run()` is now a thin loop over them). The refactor is behaviour-preserving: the 189 research tests pass and the seven full-period real-data runs reproduce their original M1 checksums bit-for-bit after the change.
* Optional `PortfolioHooks` (default `None` ⇒ M1 behaviour) let a coordinator add *other* accounts' exposure to the per-asset/total caps, feed *global* equity to the daily-loss / drawdown breakers, and block entries on stale data. A single shared `RiskEngine` instance is used across accounts.
* `RiskEngine(auto_reset_daily=False)` for paper: a daily-loss trip is latched across midnight and released only by an operator `rearm_daily` on a later UTC day; a drawdown halt is latched for the whole session. (M1 keeps the ADR-0007 auto re-arm.)
* Instruments, rules, fee schedules, execution assumptions: `SymbolRules`/`RuleProfile`, `FeeSchedule`, `ExecutionModel` are reused; the paper session stores the exact profiles it runs with.

## 3. Dependency map

```
exchanges/{binance,okx} ──(public REST/WS, egress-checked)──▶ feed collector (paper/collector.py)
                                                                │ closed 1m candles, quote snapshots
                                                                ▼
                                                   market_observations / market_data_gaps (PostgreSQL)
                                                                │ complete buckets only (research.data.resample rules)
                                                                ▼
                                   durable_jobs  STEP(session, bar_ts)  ──lease──▶ worker (paper/worker.py)
                                                                                    │ drives
                                      PaperRuntime ─ SubAccount[venue,strategy] ─ research.sim.Engine (shared RiskEngine, PortfolioHooks)
                                                                                    │ one transaction per step
                                                                                    ▼
                    paper_step_inputs, paper_orders(+events), paper_fills, paper_ledger_entries, paper_equity_snapshots,
                    paper_risk_events, strategy_signals, paper_session_checkpoints, paper_balances
```

`asta.paper` may import `asta.core`, `asta.research`, `asta.risk.policy`, `asta.db`. It must **not** import `asta.exchanges`, `httpx` or `websockets`; only `paper/collector.py`'s adapter wiring (in `asta.paper_service`) touches adapters, and `asta.exchanges` must never import `asta.paper`. Import-boundary tests enforce both directions (see §6).

## 4. Key design decisions (ADR-0009 .. 0011)

1. **Deterministic replay is the recovery mechanism.** The only inputs to the simulation are (a) the frozen session config and (b) the exact bars consumed at each step, stored in `paper_step_inputs`. State (`Engine`s) is rebuilt after any crash by replaying stored steps; the rebuilt checkpoint digest and row counts are compared with the durable rows. Mismatch ⇒ session fails closed (`FAILED`, incident, alert). No fill is ever "reconstructed from assumed prices" — a replay only re-derives what the identical recorded inputs deterministically imply, and is verified against the persisted fills.
2. **Effectively-once ledger effects.** A step = one transaction: job completion + step input + all new orders/events/fills/ledger entries/equity/risk/signals + session cursor. Uniqueness: `(session, account, fill_id)`, `(session, account, ledger_seq)`, `(session, step_ts)`, `(session, account, order_id, event_seq)`. A re-delivered job finds the cursor already advanced and is a no-op.
3. **PostgreSQL work queue.** `durable_jobs` with `FOR UPDATE SKIP LOCKED` leasing, lease expiry, heartbeats, bounded retries with backoff, failure classification (TRANSIENT / PERMANENT), terminal `DEAD` state, idempotent creation by unique `job_key`. No Redis/Kafka: one host, one database, tens of jobs per hour — no concrete limitation requires them.
4. **Sub-accounts.** One `Engine` per (venue, strategy), each with its own virtual wallet slice (venue wallet ÷ enabled strategies unless weights are configured); portfolio equity = Σ accounts. Passive benchmarks run as clearly labelled **shadow accounts** that do not count toward portfolio equity or global risk: an unconstrained reference (3×333) and a reference-policy-constrained hold (3×100).
5. **Global risk.** Per-asset (10%), total (30%), daily-loss (2%) and drawdown (5%) judged on *global* starting/current equity across venues and strategies. A halt blocks risk-increasing orders and cancels open buys; sells/exits remain allowed (documented behaviour); daily trip latches until operator re-arm on a later UTC day; drawdown halt latches for the session.
6. **Market data → bars.** The collector stores closed 1m candles (WS `kline_1m`; OKX candle channel / REST) plus a quote snapshot per minute; gap detection on 1m continuity, bounded REST backfill, gap records. Session bars (default 1h, configurable 5m/15m) are aggregated from **complete** 1m buckets only (never partial); a missing minute blocks the bar until backfilled, and if backfill fails the affected symbols are blocked from entries (stale/gap policy). Bootstrap warm-up history comes from REST klines.
7. **Services.** API process stays as is; a separate `python -m asta.paper_service` process runs collector + worker, independent of any browser/API request.
8. **No external order path.** Paper orders exist only as rows in `paper_*` tables produced by the simulator. No class in `asta.paper` has a network client, signer or credential field; settings still refuse credential env vars.

## 5. Stages and gates

| Stage | Scope | Gate |
|---|---|---|
| M2-A | collector, observation/gap tables, bars-from-1m, OKX/Binance live evidence | parsing/gap/dup/out-of-order/stale tests; live runtime evidence |
| M2-B | engine incremental API (done), runtime, session state machine, durable jobs, store | sim parity tests, queue tests, effectively-once tests |
| M2-C | global risk, recovery by replay, reconciliation, incidents/alerts, fault injection | fault-injection suite green |
| M2-D | API, UI, daily reports, health | RBAC/API tests, browser acceptance |
| M2-E | regression, scans, supervised observation run | evidence document |

## 6. Test matrix (summary)

Public data: parsing (both venues), connect/heartbeat/reconnect, gaps, duplicates, out-of-order, stale, rate-limit, clock, provenance, disabled-venue state · Paper execution: buy/sell, fees, slippage, partials, cancel, insufficient funds/inventory, duplicate signals/orders, effectively-once posting, failed transaction rollback, state transitions · Risk: per-asset, global, per-venue wallets, daily/drawdown halts + latching, negative cash/inventory, cross-strategy aggregation, reconciliation · Reliability (fault injection): crash mid-step, restart with open orders, lease expiry, concurrent workers, DB interruption, stale data, clock drift, unclean shutdown, idempotent restart, durable reporting · UI/security: auth/RBAC (direct API), session lifecycle, portfolio/risk/incident/report pages, mobile, no execution controls, import-boundary + live-lock + route-allowlist regression, full M0/M1 regression.

## 7. Honest scope statement

The 72-hour supervised observation gate cannot be executed inside a single engineering session. M2 will deliver the platform, the fault-injection evidence, a gate script and runbook, and whatever supervised observation time can truthfully be recorded; the status will not claim acceptance without the full 72 hours.
