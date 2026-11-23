# Step F — tiny real testnet orders (Binance Spot Testnet, virtual funds): readiness, runbook, drills

**Status 2026-11-21: BUILT (tooling, guards, fill ingestion and reconciliation), NOT STARTED.** No real order has been or can be placed: the gateway constant ships False, no key exists,
the probe (step E) has not run, the service is not installed and the host is not hardened. `python3 scripts/step_f_preflight.py` prints exactly what blocks
(read-only; exits 1 until every item is PASS).

## Guards built (each is enforced by code and tested)
| Guard | Where |
|---|---|
| Step E before F: the service's `testnet` mode refuses to start without a **fresh (≤ 7 days) passing probe stamp for exactly that key** | `run_service.py`, `probe.py`, `tests/security/test_step_f.py` |
| The reviewed constant `TESTNET_EXECUTION_ALLOWED` (ships False) | `risk/gateway.py`, matrix test |
| Runtime enablement by a listed releaser, ≤ 24 h, off by default, kill-switch-off, never revived by a release | migrations 0013/0014 |
| The service re-checks enablement, caps (20 USDT/order, 200 USDT/day proposed), symbol list, kill switch; persistent day spend | `credential_service.py` |
| Cancel and query keep working when new orders are closed (kill latch, expired/disabled enablement) | `authorize_reducing`, tests |
| The only order path is the drill below; it cannot size a market order, only a resting limit well inside the caps | `src/asta/orders/testnet_session.py` |

## Preconditions (all must be true; the preflight checks what a program can check)
1. Step E probe PASSED on this Mac (stamp fresh). 2. Host hardened (FileVault, firewall, no auto-login, SMB off, SSH closed or key-only). 3. No-VM service installed
(`NO-VM-DEPLOYMENT.md` §6) with `"mode": "testnet"` and the approved caps. 4. A **releaser** listed (one login per human). 5. Off-host heartbeat URL file set and the monitor timer running.
6. The reviewer re-review of the final state recorded. 7. **Your written approval for this session**, including the constant flip below.

## The session (each time; nothing here is automatic)
1. **Flip the constant in its own commit** (never bundled): in `src/asta/risk/gateway.py` set `TESTNET_EXECUTION_ALLOWED: bool = True`, commit with the message
   "TESTNET EXECUTION ALLOWED (session <date>)", push, hosted CI green. After the session, set it back to False in another commit (default stance: OFF).
2. A listed releaser enables for **the shortest useful window** (e.g. 2 h): `SELECT enable_testnet_execution(2, 'testnet drill <date> approved by <owner>');` (one login per human).
   The monitor sends an `EXECUTION_ENABLED` warning off-host. Write the unix time of the window's end to the service's enabled file (`/var/asta-cred/enabled`).
3. Start the service (`launchctl bootstrap …`), confirm `step_f_preflight.py` shows no BLOCKED.
4. **Drill 1 — place / confirm / cancel / confirm** (one resting order, away from the market): write a config with `socket`, `auth_key_file`, `key_id`, `sender`, `symbol`,
   `quantity` (e.g. 0.0001 BTC ≈ 5 USDT), `limit_price` (≈ 0.5 % below the current testnet price) and `reference_price` (the current testnet price), then
   `python -m asta.orders.testnet_session --config FILE --confirm-testnet`. Expect `ok: true`, steps ACCEPTED → ACCEPTED → CANCELED → CANCELED.
5. **Drill 2 — kill switch:** place a second drill order, latch the kill switch (`trip_kill_switch`), confirm: new orders refused, **cancel still works**, the enablement shows AUTO_OFF.
6. **Drill 3 — crash recovery:** kill the credential service between place and confirm; restart; `Dispatcher.recover()` finds the order by client id (exactly one order at the venue).
7. **Close:** `SELECT disable_testnet_execution('drill finished');`, remove the enabled file, stop the service, flip the constant back to False in its own commit.

## Fill ingestion and venue reconciliation (BUILT 2026-11-21, offline)
* **Wire:** two new read-only operations, `TRADES` (one page of an order's trades, oldest first) and `BALANCES` (non-zero account balances). Replies may carry a bounded data table
  (≤ 64 rows × 8 cells × 48 characters); all of it is authenticated and bound to the request like every other reply.
* **Gate:** `GatedExchange.trades/balances` use the same gate as query/cancel (denied while the gateway is DENY; **never** blocked by the kill switch or by an expired enablement — reading the truth only reduces risk).
* **Ingestion (`src/asta/orders/venue_sync.py`):** `sync_order` pulls every trade of an order (paged), records each through the SQL fill store — idempotent by trade id; the same id with different
  content is a CONFLICT written to the append-only log, never absorbed; more than 160 trades for one order is refused as an anomaly.
* **Reconciliation:** `reconcile_session` compares order states (venue answers only: an unreachable venue is "unavailable", never "missing") and **balances** (baseline + what OUR ledger recorded
  versus the venue account). A serious finding (`ORDER_MISSING`, `STATE_MISMATCH`, `BALANCE_MISMATCH`) or any trade conflict latches the kill switch (`RECONCILIATION_ANOMALY`).
* **Use:** before the first order of a session `python -m asta.orders.venue_sync --config FILE --snapshot` saves the baseline balances; after any order or at the end of the session
  `… --reconcile` prints the report (exit 1 if not clean). The config adds `baseline_file` to the drill config keys.
* **Open orders:** `--reconcile` also lists the orders resting at the venue per symbol and treats any id we never created as `UNKNOWN_AT_EXCHANGE` (kill switch). Add `"symbols": ["BTC-USDT", "ETH-USDT"]` to the sync config to scan more than the session symbol. Only OPEN orders are listed.
* **Not covered:** a stranger order that already finished (seen only via the balance check); strategy-driven trading itself is a later step. Real money is a separate decision.
