# Risk-halt bridge: persistent paper risk halts -> the centralized kill switch (M3-B-R2)

Status: offline logic and tests; **the bridge process is not yet deployed or supervised** (open item R-D1b). No credential, connector or signing capability.

## 1. Audit of the interactions (2026-11-14)
```
 paper runtime ──risk trip──► paper_sessions.state = RISK_HALTED, paper_session_states row, paper_risk_events
        (no import of asta.orders, by architecture test)
                 │ read-only SQL
                 ▼
 orders/risk_bridge.py ──trip_kill_switch()──► kill_switch_state (latched row) ──► kill_switch_events + audit chain
                                                         │
   Dispatcher ─► GatedExchange ─► InternalGate ─ reads ──┘   (and DB trigger: no ... -> DISPATCHED while latched)
                                   └─ core.order_gate (EXTERNAL = DENY) └─ risk.gateway.ExecutionGateway (DENY)
```
Findings of the audit: (1) before this change `Trigger.RISK_HALT` had no producer; (2) `DAILY_CAP_HIT`, `CLOCK_SKEW`, `RATE_LIMIT_BAN`, `EXCHANGE_RESET`, `AUTH_FAILURES` still have no producer (they need a connector or a measuring component; recorded, not hidden); (3) the in-memory `halts.KillSwitch` is a test double of the durable `SqlKillSwitch` and is not used by any gate; (4) the API status probe is the only production caller of `ExecutionGateway`.

## 2. Properties and tests (`tests/integration/test_risk_bridge.py`, PostgreSQL)
| Property | Mechanism | Test |
|---|---|---|
| Latch only: never releases | no release code path (AST test), release exists only as `release_kill_switch()` (listed releaser + reason) | `..._never_releases_even_when_the_session_is_re_armed` |
| At most one trip per halt event, across restarts and concurrent workers | `risk_halt_bridge_seen` PRIMARY KEY inserted in the SAME transaction as the trip; append-only | restart test, 8-thread test |
| A released halt is not re-latched by the same old event; a NEW halt event trips again | event keys: `paper_session_states.id` / `paper_sessions.id` | restart test |
| A halted session with no transition row still trips | backstop on `paper_sessions.state` | `..._without_a_transition_row...` |
| Fail closed when the risk state cannot be read | `max_read_failures` consecutive read failures (default 2) trip; no trip spam while latched | `..._unreadable_..._fails_closed` |
| Fail closed on a session state the bridge does not know | known set mirrors `asta.paper.states.SessionState` (drift test) | unknown-state + drift tests |
| Audited | each trip appends to the hash chain | audit test |

## 3. Residual
The bridge must run as a supervised process (`python -m asta.orders.risk_bridge`, poll 5 s): a launchd unit or the paper supervisor's task list, outside this change. Until it runs, paper halts do not latch the order path (which cannot send anything in this build). The failure counter is in memory (a restart resets it; the persisted trip is unaffected).
