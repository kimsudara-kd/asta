# Binance vs OKX capability matrix (public market data, M0)

Retrieved 2026-11-12. "Verified" = read from the official source below on that date. "UNVERIFIED" = could not be read from an official source in this environment.

## Sources
- Binance Spot API docs (GitHub, official): testnet/rest-api.md, web-socket-streams.md, testnet/web-socket-streams.md, filters.md at https://github.com/binance/binance-spot-api-docs
- OKX API v5: https://www.okx.com/docs-v5/en/ — **not reachable from this machine** (see "Environment finding").

## Binance (Verified)
| Item | Value |
|---|---|
| Testnet REST base | `https://testnet.binance.vision/api` |
| Testnet WS | `wss://stream.testnet.binance.vision/ws` (combined: `/stream?streams=`) |
| Market-data-only WS | `wss://data-stream.binance.vision` (no user data streams) |
| Production WS | `wss://stream.binance.com:9443` / `:443` |
| Time | `GET /api/v3/time` (weight 1) |
| Rules | `GET /api/v3/exchangeInfo` (weight 20); filters PRICE_FILTER(tickSize), LOT_SIZE(minQty,maxQty,stepSize), MIN_NOTIONAL/NOTIONAL, MAX_NUM_ORDERS |
| Klines | `GET /api/v3/klines` weight 2, default 500, max 1000 |
| Depth | `GET /api/v3/depth` weight 5 (limit<=100) |
| Book ticker | `GET /api/v3/ticker/bookTicker` weight 2 (one symbol) |
| Streams | `<sym>@trade`, `<sym>@kline_<interval>`, `<sym>@bookTicker`, `<sym>@depth5/10/20[@100ms]`; symbols lowercase |
| WS lifetime | 24h then disconnect; server ping every 20s, pong within 1 min; max 1024 streams/conn; 5 incoming msgs/s; 300 connection attempts / 5 min / IP |
| Rate limiting | HTTP 429, `Retry-After`, `X-MBX-USED-WEIGHT-*` header; IP bans 2 min–3 days |
| Testnet caveats | Testnet may reset data / lacks `/sapi/*` (handoff statement; not confirmed in the pages read) |

## OKX (UNVERIFIED against official docs)
Taken from the handoff and third-party search results only; each item is re-checked at runtime by contract fixtures and must be confirmed against the official page before M3:
- REST `https://www.okx.com`; public WS `wss://ws.okx.com:8443/ws/v5/public`; demo trading via header `x-simulated-trading: 1` (REST) — demo WS host (`wspap.okx.com`) vs header **disagree between sources**.
- `GET /api/v5/public/time`, `GET /api/v5/public/instruments?instType=SPOT` (fields tickSz, lotSz, minSz), `GET /api/v5/market/candles`, `GET /api/v5/market/books`; public WS channels `tickers`, `trades`, `candle1m`, `books5`; text `ping`/`pong` keepalive (documented interval to be confirmed).

## Environment finding (blocker for live OKX data)
On this PC `www.okx.com` and `api.binance.com` resolve to `127.0.0.1` (blocked at the local DNS/network level; no hosts-file entry). `data-api.binance.vision` and `testnet.binance.vision` are reachable. No attempt is made to bypass the block (region/regulatory restrictions must not be circumvented). Consequences: Binance public data is live via `data-api.binance.vision`; OKX adapters are tested against recorded fixtures and the dashboard labels OKX data `FIXTURE`/`UNREACHABLE` until reachability is resolved by the owner.

## Differences that matter
| Topic | Binance | OKX |
|---|---|---|
| Symbol | `BTCUSDT` | `BTC-USDT` |
| Size/price rules | `stepSize` / `tickSize` / notional filters | `lotSz` / `tickSz` / `minSz` |
| Demo | separate host + keys | header + demo keys |
| Keepalive | server ping frames | text `ping` |
