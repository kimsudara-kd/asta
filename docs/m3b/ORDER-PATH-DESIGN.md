# M3-B order path: design (specification and offline proof only)

**This document authorizes nothing.** M3-B is NO-GO (`docs/m3a/M3B-READINESS.md`; F-01, F-02, F-03 open). No connector, credential, signing code, network call or order exists in this repository, and `tests/live_lock` still proves it. What exists is **pure logic with a fake exchange** (`src/asta/orders/`, 328 tests) so that readiness blocker **B-6 / finding F-12 (duplicate or runaway orders)** is specified and proven *before* any order path is built.

## 1. Scope proposed for a future authorization
Binance Spot **Testnet** only; spot BUY/SELL, MARKET and LIMIT; one connector instance bound to `EXCHANGE_DEMO`; OKX excluded until B-5. Cancel/amend are a later increment (see s.7). Limits below are **proposals**, not decisions.

## 2. Components and trust boundaries
```
 strategy (asta-app, no credentials)            asta-cred (vault + connector, only credential holder)
   decision -> decision_id (order_identity)       re-validates EVERYTHING (does not trust the app)
   -> Dispatcher: claim, guard, write-ahead  -->  intent channel (outbound-only from app, one TCP port,
                                                   authenticated, size-limited) --> connector -> exchange
```
* The app VM never holds a credential and cannot sign; the connector VM never runs strategy code. Both enforce the pre-trade guard (defence in depth); the connector's copy is authoritative.
* The connector's only egress is asta-fw's proxy to the allowlisted testnet host (the 22/22 enforcement lab, `MAC-A11`, covers the rule sets, not the connector).
* The gateway stays default-deny; enabling the order path needs the signed two-reviewer privilege configuration (`verify_privilege_config`).

## 3. Identity (done, `order_identity.py`)
`decision_id` = 128-bit deterministic hash of (session config hash, account, symbol, side, type, tag, UTC decision time, ordinal); it is also the exchange client order id. Recovery never creates another id.

## 4. The protocol (implemented, `orders/dispatch.py`)
1. **CLAIM** the decision durably (`insert_if_absent`, a UNIQUE constraint in the real store). A repeated delivery stops here and **never** triggers a send.
2. **GUARD** (`orders/pretrade.py`): kill switch, environment must be `EXCHANGE_DEMO`, venue/symbol allowlists, per-order notional, daily order count, daily notional, limit-price band; no limits configured or an unsizable intent **refuses** (fail closed). All applicable reasons are reported. A refused decision is stored `REJECTED` (audit) and is not retried.
3. **WRITE-AHEAD**: state `DISPATCHED` is persisted *before* the request leaves.
4. **SEND** with client id = `decision_id`. ACCEPTED / REJECTED are final; DUPLICATE_ID means "we already sent it" -> ask by id; anything ambiguous -> `UNKNOWN`.
5. **RECOVER** (after a timeout, crash or restart), for `UNKNOWN`/`DISPATCHED`: query the exchange **by client id**. FOUND -> adopt its state. NOT_FOUND -> count it; only after **N consecutive NOT_FOUND answers in separate recovery rounds** (default 2) re-send the **same** id; after `max_attempts` or `max_age`, or under a kill switch, mark `ABANDONED`. An `ABANDONED` decision that later appears at the exchange is still adopted. UNAVAILABLE changes nothing. Live orders are synchronised until terminal.

States: `PENDING -> DISPATCHED -> {UNKNOWN <-> DISPATCHED} -> ACCEPTED -> PARTIALLY_FILLED -> FILLED | CANCELED | REJECTED | EXPIRED`; `ABANDONED` may still adopt an exchange outcome; terminal states have no exit. Illegal transitions raise.

## 5. Why submission is exactly-once, and what that rests on
Invariants proven by the model (300 seeded chaos runs with request loss 20 %, reply loss 25 %, queries unavailable 30 %, delayed visibility, crashes injected after any durable write and after a send, and 1-3 repeated deliveries per decision): (A) every exchange order carries one of *our* decision ids (no minted ids); (B) nothing stays unresolved once the exchange is healthy; (C) every claimed decision has **exactly one** exchange order; (D) local state equals exchange state for everything that exists. Mutation checks confirm the tests fail if the claim, the query-before-resend or the late-adoption is removed.

It **rests on assumptions about the real exchange that the fake cannot prove** (all UNVERIFIED until a demo key exists, readiness B-5/B-7):
* **U-1 duplicate refusal:** Binance refuses a repeated `newClientOrderId` ("Duplicate order sent"). The docs also say an id may be accepted again once the previous order with it is **filled** (stated in the SOR section; whether it holds for `/api/v3/order` is unverified). If so, a fast-filled market order whose reply is lost and which is momentarily invisible to a query could be **doubled by a same-id re-send**. Mitigation implemented: re-send only after consecutive NOT_FOUND answers (`not_found_confirmations`, default 2); the test suite demonstrates that one confirmation doubles the order on such an exchange and two do not. Still required before any demo order: measure real query visibility latency on the testnet, size the confirmation count / quiet period from it, and add a trade-history cross-check (`myTrades`/balance delta) before a re-send. **If the question cannot be answered, prefer ABANDON + alert over re-send (missing a trade costs less than doubling one).**
* **U-2** query-by-client-id returns filled and cancelled orders for long enough; **U-3** REST client-id character rules (`^[a-zA-Z0-9-_]{1,36}$` is documented for FIX; our 32 hex characters fit any plausible rule); **U-4** the testnet monthly reset is handled as an expected event.

## 6. Kill switch and automatic halts
Kill switch (manual, or automatically on: `RISK_HALT`, reconciliation anomaly, an order found at the exchange that is not in the store, repeated auth failures, daily caps hit) means: no new dispatch, recovery keeps adopting, pending/unknown work is abandoned (and adopted if it appears later). It is a pure input of the guard (`DayState.kill_switch`) so it is testable offline.

## 7. Not designed yet (explicit gaps)
Cancel and amend flow and kill-switch cancellation of resting orders; partial-fill handling beyond state adoption; the durable store schema (UNIQUE `decision_id`, same-transaction write-ahead) and migration; the intent-channel wire format and its authentication; reconciliation of balances vs ledger and the paper-vs-demo report; rate-limit budgeting (testnet 50 / 10 s); clock-skew handling; the real vault backend (B-4).

## 8. Failure-mode table
| Failure | Handling | Evidence |
|---|---|---|
| Same signal delivered twice | claim stops it; no send | `test_a_repeated_decision...`, chaos |
| Crash after claim, before write-ahead | PENDING -> never sent -> recovery sends | chaos (crash injection) |
| Crash after write-ahead / after send | DISPATCHED -> UNKNOWN -> query by id -> adopt or same-id re-send | chaos |
| Reply lost / timeout | UNKNOWN -> query by id; never a new id | `test_a_lost_reply...` |
| Request never arrived | N x NOT_FOUND -> same-id re-send | `test_a_request_that_never_arrived...` |
| Order in flight but invisible to query | confirmations + exchange duplicate refusal | `test_an_in_flight_order...`, hazard tests |
| Exchange unreachable | no state change; retried later | `test_exchange_unavailable...` |
| Stale or too many attempts | ABANDONED; adopted if it appears | `test_max_attempts...`, `test_a_stale...` |
| Kill switch | nothing new sent; pending abandoned | `test_kill_switch...` |
| Exchange contradicts local state | anomaly reported, no crash | `test_an_exchange_report_that_contradicts...` |
| Strategy re-emits the same refused entry each bar | different decision each bar: reduced by the opt-in repeat guard (default OFF), not by ids | `PAPER-REJECTION-ANALYSIS.md` |

## 9. What must happen before any of this is connected
B-1..B-7 of the readiness document (independent review, boundary deployed on the dedicated host, real vault backend and key ceremony, durable store and wire protocol designed and reviewed, U-1..U-4 measured on the testnet with a demo key created by the account owner), and a fresh M3-B directive with its own acceptance gates. Nothing in this file or in `src/asta/orders/` may be treated as that authorization.
