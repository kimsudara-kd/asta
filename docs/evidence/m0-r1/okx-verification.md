# OKX verification and blocker report (M0-R1, Workstream A)

Date: 2026-11-12 · Machine: dev PC (Windows 11, Wi-Fi, home router) · Result: **live OKX verification BLOCKED by the network; offline verification complete**

## 1. Diagnosis of the loopback resolution (read-only; nothing was changed)
| Check | Result |
|---|---|
| `hosts` file | no non-comment entries |
| Proxy (WinHTTP, user proxy, PAC) | none (direct access) |
| Windows DNS-over-HTTPS | well-known servers registered but `AutoUpgrade=False`; not in use |
| Security software | Windows Defender only; no DNS-filter/VPN/WARP/AdGuard/NextDNS service running |
| Configured resolvers (Wi-Fi) | `192.168.20.1` (home router), `100.127.1.11`, `100.127.2.11` (ISP resolvers, CGNAT range) |
| Resolution of `www.okx.com`, `aws.okx.com`, `okx.com`, `api.binance.com` | **`127.0.0.1` from the system resolver AND from each of the 3 configured resolvers queried directly with `nslookup`** |
| Control hostnames (`example.com`, `github.com`, `pypi.org`, `data-api.binance.vision`, `testnet.binance.vision`) | resolve to real addresses; TCP/HTTPS to them works |

**Source of the restriction:** network-level DNS sinkholing performed by the upstream resolvers (the router and the ISP's resolvers all return the same loopback answer for these specific exchange API hostnames and only those). It is not a local misconfiguration, hosts entry, proxy, or antivirus. This is consistent with deliberate ISP/regulatory blocking of crypto-exchange domains; the *reason* and the party responsible cannot be determined from this machine and is not asserted.

**Not done, by design:** no alternate DNS/DoH resolver, VPN, proxy, hosts-file edit, hard-coded IP, or other workaround was used or tested, and no security setting was changed. Doing so would circumvent a deliberate access control (and possibly a regional restriction), which the handoff forbids.

## 2. Documentation retrieval
| Source | Outcome |
|---|---|
| `https://www.okx.com/docs-v5/en/` (official) | **unreachable** (same DNS block; fetch tool: `ECONNREFUSED 127.0.0.1`) |
| `web.archive.org` copy | fetch refused by the tool |
| OKX official SDK `github.com/okxapi/python-okx` (reachable; official OKX organisation) | **read** — `okx/consts.py`, `okx/okxclient.py`, `okx/utils.py`, `okx/MarketData.py`, `okx/websocket/WsPublicAsync.py`, `README.md` (retrieved 2026-11-12) |

**Confirmed from the official SDK:** REST base `https://www.okx.com`; paths `/api/v5/public/time`, `/api/v5/public/instruments`, `/api/v5/market/books`, `/api/v5/market/candles`, `/api/v5/market/tickers|ticker|trades`; parameters `instId`, `bar`, `limit`, `sz`, `after`, `before`; demo mode = header `x-simulated-trading` set from `flag` (`0` live, `1` demo); WS subscribe payload `{"op":"subscribe","args":[...]}`; the SDK README points to the official doc pages for production/demo URLs.

**NOT confirmed (no accessible official source):** WebSocket URLs (`wss://ws.okx.com:8443/ws/v5/public`, demo host), the text `ping`/`pong` interval, rate limits, exact response field lists (`tickSz`/`lotSz`/`minSz`/`state`, ticker/trade/candle payloads). These remain "per handoff and third-party sources". `docs/exchange-matrix/capability-matrix.md` marks each accordingly. The adapters use only public, unauthenticated endpoints; the demo header is not sent in M0.

## 3. What was verified without live access
`tests/contracts/test_okx_offline.py` (6 tests) and `test_adapters_fixtures.py`:
- REST normalisation (time, instruments → `SymbolRules` with exact Decimal `tickSz/lotSz/minSz`, candles with incomplete-candle flag, order-book best quote) against mocked responses; error code `50011` and HTTP 429 are raised, never retried or swallowed.
- Clock behaviour: venue clock 10 s ahead ⇒ drift not OK; in sync ⇒ OK (fail-closed on unknown).
- WebSocket against a **real local server**: subscribe message, text `ping` → `pong` keepalive (pongs filtered from the parser), forced server drop, automatic reconnect with backoff, re-subscribe, replayed event de-duplicated (3 accepted / 1 duplicate / 2 connections), gap marked then closed.
- Normalisation of `tickers`, `trades`, `candle1m` frames; subscribe acks ignored.
- Egress: OKX public hosts allowed; private/trading/asset/account paths and private WS refused.

These prove our code handles the *assumed* OKX shapes. They do **not** prove the assumptions match OKX's live service.

## 4. Blocker report
| Item | Status |
|---|---|
| B-OKX-1 live public REST/WS verification (time, instruments, candles, books, streams, clock offset, real symbol rules) | **BLOCKED** — DNS sinkhole upstream of this PC |
| B-OKX-2 current official docs for WS URLs, keepalive, rate limits, field lists | **BLOCKED** — same cause; partially mitigated via official SDK |

**Unblock options (owner decision; none attempted):**
1. Run the opt-in live tests from a network where OKX access is lawful and permitted for this operator (e.g. an approved office/hosting network). Command: `ASTA_RUN_NETWORK_TESTS=1 uv run python -m pytest tests/contracts/test_live_public.py -rs` — the OKX test currently skips ("OKX unreachable") and will then run for real.
2. Supply an exported copy of the official OKX API v5 docs (PDF/HTML) obtained through an authorised channel, to close B-OKX-2.
3. Accept M0 with OKX recorded as *fixture/offline-verified only* and make live OKX verification a precondition of M1/M3 (CEO decision).

Region/legality of using OKX from this location is a compliance question for the CEO, not an engineering workaround.
