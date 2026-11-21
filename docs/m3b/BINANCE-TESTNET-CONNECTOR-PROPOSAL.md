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
| D | Credential VM deployed, egress profile for `testnet.binance.vision`, host hardened, off-host alerts live | host work (yours), VM provisioning |
| E | First network contact: **`POST /api/v3/order/test`** (validates a signed order, places nothing) with a testnet key in the vault | key ceremony, your written approval |
| F | Tiny real testnet orders (virtual funds), reconciliation vs venue, kill-switch and recovery drills | your written approval per session |

## 7. Decisions needed from you
1. Option A (recommended) or B? 2. Approve step B (code under `src/`, gateway still DENY)? 3. Notional caps for the testnet pilot (proposal:
≤ 20 USDT per order, ≤ 200 USDT per day, symbols BTC-USDT/ETH-USDT)? 4. Who are the releaser humans (one login each)?

## 8. Known limits of what exists now
The mock is ours: it proves our mapping and exactly-once logic, **not** the venue's real behaviour (e.g. exact error texts, filter rules,
`-1021` handling, partial-fill timing). Step E is the first time that is learned, which is why it uses `order/test` first.
The signer in `proposals/` is a **test double** (in-process key). A vault-backed signer (non-exportable key) replaces it before step E.
