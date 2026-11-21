# Binance Spot Testnet connector — design and lock-replacement proposal

**Status: PROPOSAL for owner review. NOTHING under `src/` was changed. No credential exists, no network request is made, the
gateway is still a constant DENY.** Scope: Binance **Spot Testnet** (`testnet.binance.vision`), virtual funds, spot BUY/SELL
MARKET/LIMIT only. Real money is a separate, later decision and is NOT covered here.

## 1. What was built (staged in `proposals/binance_testnet_signer/`, outside `src/`)
| File | Role |
|---|---|
| `signer.py` | Synthetic Ed25519 signer + `build_signed_request` (testnet host only, spot path allow-list, per-path **parameter allow-list**, recvWindow ≤ 5000) + `mock_endpoint_verify` |
| `connector.py` | `BinanceTestnetPort` — implements `asta.orders.ports.ExchangePort` (`send` / `query` / `cancel`) over an **injected transport**; no socket/HTTP code in it |
| `mock_exchange.py` | In-process mock of the testnet order endpoints: verifies signature/window/replay, keeps orders by client id, injects timeout-before / timeout-after / 5xx-after / 429 |
| `tests/security/test_testnet_connector.py` | 15 tests: mapping, exactly-once, fail-closed, no-network-imports |

### Outcome mapping (conservative: ambiguous ⇒ UNKNOWN, never a terminal guess)
| Venue answer | `send` | `query` | `cancel` |
|---|---|---|---|
| 200 NEW / PARTIALLY_FILLED / FILLED | ACCEPTED | FOUND(state) | 200 CANCELED → CANCELED |
| 200 REJECTED/EXPIRED with executedQty 0 | REJECTED | FOUND | — |
| 400 `-2010` "Duplicate order sent" | **DUPLICATE_ID** (our own decision id) | — | — |
| 400 `-2010` balance, `-1013` filters, `-2014/-2015` auth, `-11xx` malformed | REJECTED | — | — |
| 400 `-2013` order does not exist | — | NOT_FOUND | — |
| 400 `-2011` unknown order | — | — | re-query: FINAL(state) if terminal, UNKNOWN_ORDER if absent, else UNKNOWN |
| timeout, 429/418, 5xx (venue: "send status unknown"), `-1021` clock skew, anything unrecognised | **UNKNOWN** | UNAVAILABLE | UNKNOWN |
| signer unavailable | REJECTED (nothing left the process) | — | — |
The client order id **is** the decision id (32 hex), so a re-send can never create a second order; after UNKNOWN the dispatcher queries by
client id and re-sends only after repeated NOT_FOUND (existing `Dispatcher` logic, unchanged).

> **Owner decision 2026-11-21: no VMs.** Read `NO-VM-DEPLOYMENT.md`: wherever this document says "credential VM" or "VM", read "a separate local OS user (`asta-cred`) running the connector service, reached over a Unix socket". The code and the lock-test reasoning below are unchanged.

## 2. Where signing must live — two options
**Option A (recommended): signing outside `src/`, in the credential process/VM.** The app (`src/`) only gets an
`ExternalChannelPort` that talks to the existing replay-protected channel (`asta.orders.channel`). The connector + signer ship as a separate
package (`connector/`, own tests, own stricter lock) that runs on the credential VM. `src/` never contains signing or key code, so
`test_no_signing_or_credentials_anywhere` and `test_no_order_or_funds_symbols_in_exchange_code` stay **byte-identical and unweakened**.
**Option B: signing inside `src/`.** Requires adding the connector's files to the allow-lists of those two tests (weakening). Not recommended.

## 3. The exact changes Option A needs under `src/` (specification only — not applied)
| # | File | Change | Replacement protection / test |
|---|---|---|---|
| 1 | `src/asta/orders/boundary.py` | `GatedExchange.query/cancel` currently deny every non-SIMULATED port. Allow them for `PortKind.EXTERNAL` **only when `InternalGate.authorize` returns allowed** (same gate as `send`) | new tests: every call on an EXTERNAL port is gated; query/cancel denied when killed / not enabled |
| 2 | `src/asta/risk/gateway.py` (live-lock file) | `ExecutionGateway.authorize` may return ALLOW **only if all hold**: `environment == EXCHANGE_DEMO` and `venue == BINANCE_TESTNET` and `TESTNET_EXECUTION_ALLOWED` (new constant, reviewed commit) and `LIVE_TRADING_ENABLED is False` and the runtime enablement below. `LIVE_TRADING_ENABLED` stays `False`; `Environment` still has no LIVE | `tests/live_lock/test_paper_boundaries.py:129-131` pins `EXECUTION_ENABLED_IN_THIS_BUILD is False`; it is replaced by pins on **both** constants plus a matrix test (every non-testnet venue/environment/side/size combination ⇒ DENY) |
| 3 | `src/asta/orders/` (new `external_port.py`) | `ExternalChannelPort` — builds an `Envelope`, sends it over the channel, maps the reply to `SendOutcome/QueryResult/CancelResult`. **No signing, no key, no HTTP** | covered by the existing no-signing/no-network scans (they apply to it) |
| 4 | credential-side service (outside `src/`) | receives envelopes, **re-validates everything** (host = testnet, spot paths only, size/notional caps, kill switch), calls `BinanceTestnetPort`, never returns secrets | the connector tests here + a service-level lock test |
| 5 | egress (`infra/egress`, `src/asta/core/egress.py`) | today `/api/v3/order` is in `FORBIDDEN_PATH_FRAGMENTS`. The credential VM's own egress profile (not the app VM's) would allow **only** `testnet.binance.vision` | egress negative tests stay for the app VM; new profile tests for the credential VM |
Everything else in `tests/live_lock/` is unchanged. Files that would change in the live-lock set: `risk/gateway.py` and the one assertion above — **a reviewed, owner-approved change**.

## 4. Runtime enablement (a code constant alone must never be enough)
Enabling is a **two-key, time-limited, default-off** state: (1) the reviewed constant `TESTNET_EXECUTION_ALLOWED`; (2) a database row
`execution_enablement(scope='BINANCE_TESTNET', enabled_until, enabled_by)` set only through a `SECURITY DEFINER` function callable by the
**releaser** roles (`rel_<name>`, one login per human), with an expiry (default ≤ 24 h), a reason, an audit-chain entry, and an off-host alert when
it changes. The kill switch (latched) overrides everything. No AI component can hold a releaser login or call that function.

## 5. Safety properties the design keeps
Spot only (no futures/margin/borrow/transfer/withdraw — absent from the signer's parameter allow-list, the connector, and the egress profile);
testnet host only (signer refuses any other base URL); per-order and per-day notional caps and symbol allow-list in the existing `pretrade` checks;
exactly-once via deterministic ids + write-ahead states; fail closed on signer/vault/channel/clock problems; the app VM never holds a key;
production Binance hosts appear nowhere (tests assert it).

## 6. Staged path (each step needs your approval; none is taken)
| Step | What | Needs |
|---|---|---|
| A | Offline: connector + mock exchange (**done**, 15 tests) | — |
| B | Offline integration: `ExternalChannelPort` + credential-side service + the existing `Dispatcher`, against the mock (changes #1,#3,#4, **no #2**: gateway still DENY, so it runs with a test gateway) | your OK to write `src/` code |
| C | Gateway change #2 + matrix tests + enablement function; still no credentials, no network | your OK for the live-lock change; independent review |
| D | Connector service installed as user `asta-cred` (launchd), optional pf anchor for `testnet.binance.vision`, host hardened, off-host heartbeat live | host work (yours): `NO-VM-DEPLOYMENT.md` §5 |
| E | First network contact: **`POST /api/v3/order/test`** (validates a signed order, places nothing) with a testnet key in the vault | key ceremony, your written approval |
| F | Tiny real testnet orders (virtual funds), reconciliation vs venue, kill-switch and recovery drills | your written approval per session |

## 7. Decisions needed from you
1. Option A (recommended) or B? 2. Approve step B (code under `src/`, gateway still DENY)? 3. Notional caps for the testnet pilot (proposal:
≤ 20 USDT per order, ≤ 200 USDT per day, symbols BTC-USDT/ETH-USDT)? 4. Who are the releaser humans (one login each)?

## 8. Known limits of what exists now
The mock is ours: it proves our mapping and exactly-once logic, **not** the venue's real behaviour (e.g. exact error texts, filter rules,
`-1021` handling, partial-fill timing). Step E is the first time that is learned, which is why it uses `order/test` first.
The signer in `proposals/` is a **test double** (in-process key). A vault-backed signer (non-exportable key) replaces it before step E.

## 9. Step B — DONE (offline; owner-approved 2026-11-21; gateway still DENY, no credentials, no network)
Added under `src/` (all covered by the existing live-lock scans, which were **not** modified):
- `src/asta/orders/extwire.py` — authenticated request/reply wire (SEND / QUERY / CANCEL), keyed BLAKE2b authenticator, authenticate-before-parse,
  canonical bytes, replay/expiry via `ReplayStore`, replies bound to the request nonce and operation.
- `src/asta/orders/external_port.py` — `ExternalChannelPort` (an `ExchangePort`): no signing, no exchange key, no HTTP, no socket; conservative mapping.
- `src/asta/ipc/unix_socket.py` — length-prefixed frames over **AF_UNIX only**.
- `src/asta/orders/boundary.py` — **change #1 of §3**: `GatedExchange.query/cancel` on an EXTERNAL port now go through the same gate as `send`
  (`InternalGate.authorize_reducing`: the latched kill switch does not block a cancel, the gateway still does), the intent is required, and the dispatcher passes it.
  The existing hard stop stays: even if the gateway one day returned ALLOW, `EXTERNAL_PORT_NOT_SUPPORTED_IN_THIS_BUILD` still denies until step C.
Staged outside `src/` (`proposals/binance_testnet_signer/credential_service.py`): the credential-side service — re-validates every request, enforces venue/symbol
allow-lists, per-order (20 USDT) and per-day (200 USDT) caps (proposed values, to be confirmed), refuses MARKET orders whose size cannot be verified, honours the
kill switch for sends (never for cancels), and serves a 0660 Unix socket.
Verified: 24 new tests (wire 7, port + gate 8, end-to-end 9 incl. lost reply → recovered by query with exactly one order, caps, kill switch, tamper/replay, a real
socket); declared EXTERNAL the whole chain is **denied** with zero channel calls. `tests/live_lock` and `risk/gateway.py` unchanged.
Not done (needs your approval, step C): the gateway change + enablement function; a durable replay store for the service; the launchd/`asta-cred` deployment.

## 10. Step C — DONE (offline; owner-approved "start step c" 2026-11-21): the gateway can now allow exactly one cell, and ships OFF
**Live-lock change (reviewed, owner-approved): `src/asta/risk/gateway.py`.** `tests/live_lock/*` are unmodified; the gateway file no longer equals `fb7770b`
(that baseline claim in earlier packages now reads "unchanged until step C").
| Condition for ALLOW (all required) | Where it lives | Ships as |
|---|---|---|
| environment is exactly `EXCHANGE_DEMO` and venue exactly `BINANCE_TESTNET` | `ExecutionGateway.authorize` | — |
| reviewed code constant `TESTNET_EXECUTION_ALLOWED` | `risk/gateway.py` | **False** (a separate reviewed, owner-approved commit flips it) |
| `LIVE_TRADING_ENABLED` is False | `risk/gateway.py` | False (unchanged, still pinned by the old test) |
| `EXECUTION_ENABLED_IN_THIS_BUILD` (general flag) | `risk/gateway.py` | False (never opens anything but the testnet cell) |
| runtime enablement active | DB migration **0013**, read through `SqlEnablement` (fails closed) | **OFF** |
| kill switch not latched | `InternalGate` (sends) + `execution_enabled()` (SQL) | — |
**Runtime enablement (migration 0013):** `enable_testnet_execution(hours 1..24, reason ≥10 chars)` / `disable_testnet_execution(reason)` — callable only by a
**listed releaser** (`session_user`, one login per human); refuses while the kill switch is latched; writes an append-only event; a guard trigger refuses any other way
to turn it on (bare UPDATE as any role, with the GUC, or as a listed releaser without a same-transaction ENABLE event); **latching the kill switch turns it off
automatically** (AUTO_OFF event) and releasing the switch does **not** turn it back on; it expires by itself. The monitor raises an `EXECUTION_ENABLED` warning
(off-host) while it is on. Role grants are in `pg_roles_m3b.sql` (orders/monitor can only ask and read; nobody gets UPDATE).
**Boundary:** the old hard stop `EXTERNAL_PORT_NOT_SUPPORTED_IN_THIS_BUILD` is removed; an EXTERNAL port now passes only when the gateway says ALLOW (send also needs no
kill latch; query/cancel are never blocked by the kill switch itself, only by the gateway).
**Evidence:** exhaustive matrix test (8 environments × 8 venues × 4 enablement states × side × price × 8 constant combinations): ALLOW only in the one cell;
shipped constants all False; the gateway module imports nothing configurable; only `risk/gateway.py` names the constants; 8 DB tests on the real roles; 6 boundary
tests; an end-to-end chain test (enabled → reaches the mock exchange; switched off → nothing leaves the app, a cancel is refused too).
**Still not done:** flipping `TESTNET_EXECUTION_ALLOWED` (never in this work); a durable replay store for the service; the `asta-cred` launchd deployment; any real
network contact (first would be `POST /api/v3/order/test`); vault-backed signer; credentials; host hardening; reviewer re-review of the gateway change.

## 11. Step E — tooling BUILT (owner-approved "start step e" 2026-11-21); probe not yet run (needs the owner's key)
Staged in `proposals/binance_testnet_signer/`: `keygen.py` (Ed25519 pair; private key written 0400, never printed, never overwritten), `file_signer.py` (key-file signer with
strict permission checks, no key accessor), `https_transport.py` (the ONLY network code: https, host pinned to `testnet.binance.vision`, no redirects, no environment proxies,
verification cannot be disabled, size/time limits, errors never carry the URL), `probe.py` (public time + signed `order/test` + read-only account; cannot place an order).
Verified offline against a local TLS mock (11 tests: key handling, signature accepted by the mock verifier, redirect/size/junk/timeout/proxy handling, clock gate, no `POST /api/v3/order`
ever sent) and once against the REAL testnet **without credentials**: `GET /api/v3/time` → HTTP 200, clock offset ≈ 158 ms. Runbook: `STEP-E-KEY-CEREMONY-AND-PROBE.md`.
Nothing under `src/` changed in step E; the gateway constant is still False.
