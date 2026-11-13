# Exchange Capability Matrix: Binance Spot Testnet and OKX Demo Trading

Retrieval date: 2026-11-13
Scope: documentation research only. No exchange endpoint was called, no keys were created or requested. Pages were read through WebFetch / WebSearch only.

## Status legend

| Label | Meaning |
|---|---|
| DOCUMENTED | Stated on an official documentation page (read or quoted via the retrieval tool). Not tested. |
| RUNTIME VERIFIED | Observed by a real call to the exchange. In this file only the three Binance Spot Testnet PUBLIC endpoints probed in M3A-3 (time, exchangeInfo, klines), plus the Binance testnet public WebSocket upgrade, have this status. |
| FIXTURE VERIFIED | Matches a recorded or synthetic fixture in our test suite. Nothing in this file has this status. |
| UNVERIFIED | Not confirmed from an official page, or sources conflict. Do not rely on it. |
| BLOCKED | Could not be obtained (access or permission). |

Rows are DOCUMENTED unless the Connectivity test result field says otherwise; the Connectivity test result fields were filled by the lead engineer from the M3A-3 probes (below).

## Retrieval caveats (read first)

- developers.binance.com pages could not be fetched (the fetch tool returned `connect ECONNREFUSED 127.0.0.1:443`). Binance facts come from the official GitHub repository `github.com/binance/binance-spot-api-docs` (raw files, master branch) and from `https://testnet.binance.vision/`. The GitHub repo is the source of the developers.binance.com content, but wording and file layout may differ slightly from the web site.
- Several large GitHub files were only partly read by the tool (rest-api.md: only the first ~200,000 of 211,689 characters; testnet/rest-api.md and testnet/web-socket-api.md: first 100,000 characters). Items not found in the read window are marked UNVERIFIED.
- Every okx.com host (www, app, my, eea, aws, okx.com, and the help pages) returned `connect ECONNREFUSED 127.0.0.1:443` from the fetch tool. BLOCKED for direct reading. All OKX content below comes from WebSearch result summaries that quote official OKX pages (`okx.com/docs-v5/en/`, `okx.com/docs-v5/log_en/`, `okx.com/en-us/help/api-faq`). These are second-hand excerpts produced by a search tool, not full page reads, and the search tool itself noted some cached copies are old. Treat OKX rows as DOCUMENTED (excerpt) and re-read the live page before M3-B. Anything the excerpts did not show is UNVERIFIED.
- Exact URLs for the "Official source URL" fields are the URLs actually fetched or returned by search. Where the fetch failed, the URL is listed with "(fetch BLOCKED; excerpt via search)".

---

# Part A. Binance Spot Testnet

## A0. Environment facts

| Item | Value | Source | Status |
|---|---|---|---|
| REST host | `https://testnet.binance.vision/api` | https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/testnet/rest-api.md ; https://testnet.binance.vision/ | DOCUMENTED |
| WebSocket stream host | `wss://stream.testnet.binance.vision/ws` (raw: `/ws/<streamName>`; combined: `/stream?streams=...`). The depth example in the doc uses port 9443. | https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/testnet/web-socket-streams.md | DOCUMENTED |
| Market-data-only WS | `wss://data-stream.binance.vision`. The doc states user data is NOT available there. Whether a testnet equivalent exists is not stated. | same | DOCUMENTED (testnet equivalent UNVERIFIED) |
| WebSocket API host | `wss://ws-api.testnet.binance.vision/ws-api/v3` ; port 9443 also available | https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/testnet/web-socket-api.md | DOCUMENTED |
| FIX hosts | `fix-oe.testnet.binance.vision` (ports 9000 FIX in/out, 9001 FIX in/SBE out, 9002 SBE in/out), `fix-dc.testnet.binance.vision`, `fix-md.testnet.binance.vision` | https://testnet.binance.vision/ | DOCUMENTED (not planned for use) |
| Legacy URLs | `wss://testnet.binance.vision/ws-api/v3` and `/ws` were announced for removal in May 2025. Do not use. | https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/testnet/CHANGELOG.md | DOCUMENTED |
| Separate hostname from production | Yes. Testnet hosts (`*.testnet.binance.vision`) differ from production (`api.binance.com`, `ws-api.binance.com`). Host allowlisting can separate environments. | derived from the above | DOCUMENTED (derivation) |
| Supported endpoints | "Only endpoints starting with `/api/*` are supported, `/sapi/*` is not supported." | https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/README.md | DOCUMENTED |
| How keys are obtained | Log in at testnet.binance.vision with a GitHub account, then generate a key. Types: HMAC-SHA-256, RSA (2048-4096 bit, PKCS#1 v1.5, PSS not supported), Ed25519. New keys cannot trade by default; trading must be enabled in API Management. Permissions named in docs: TRADE, USER_DATA, USER_STREAM. No key was obtained for this research. | https://testnet.binance.vision/ ; testnet/rest-api.md | DOCUMENTED |
| Virtual balances | "All funds are virtual, usable only on the testnet, and cannot be moved in or out of it." New users automatically receive balances in many assets. | https://testnet.binance.vision/ | DOCUMENTED |
| Reset behaviour | "Periodically reset to a blank state, roughly once per month, with no advance notice." A reset clears all pending and executed orders and users get a fresh allowance of every asset. Changelog lists resets on 2025-06-04, 2025-07-02, 2025-09-05, 2025-10-01, 2025-11-05, 2026-01-07, 2026-02-04, 2026-05-06, 2026-06-03, 2026-08-05, 2026-09-09, 2026-10-07 ("All data on the Spot Test Network will be deleted today according to the periodic reset procedure."). The next reset date is not documented (pattern suggests early December 2026, which is an inference, UNVERIFIED). | https://testnet.binance.vision/ ; https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/testnet/CHANGELOG.md | DOCUMENTED (next date UNVERIFIED) |
| Rate limits and filters vs production | "IP limits, order rate limits, exchange filters, and symbol filters are generally the same as the production Spot API." Read current values from `GET /api/v3/exchangeInfo`. | https://testnet.binance.vision/ | DOCUMENTED |
| Testnet-only settings | Changelog: `MAX_ASSET` filter for JPY set to 1,000,000 on testnet (2025-09-24); Price Range Execution Rule on `BTCUSDT` and `BROCCOLI714USDC` "for testing only and may not be indicative of settings on the live exchange" (2026-03-16); optional commissions on testnet orders (2026-03-05); Unicode test assets (2025-12-18). Changelog statement: "This is not always synced with the live exchange." | https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/testnet/CHANGELOG.md | DOCUMENTED |
| Matching and liquidity vs production | No official page read states testnet order-book depth, liquidity, price source, or matching fidelity relative to production. Assumed not equal to production. | none | UNVERIFIED |
| Doc statement on page availability | README: "Currently, this is only available via the API." | README.md | DOCUMENTED |

## A1. Capability sections

Field names follow the requested list. Where a field is identical across capabilities, it is stated once in A0 or A2 and referenced.

### A1.1 Server time: `GET /api/v3/time`
- Official source URL: https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/rest-api.md
- Retrieval date: 2026-11-13
- Public or authenticated: public (security type NONE; "If unspecified, the security type is NONE")
- Demo vs production distinction: host (`testnet.binance.vision` vs `api.binance.com`)
- Transport: HTTP
- Auth method: none
- Required permissions: none
- Symbol/minimum-order rules: n/a
- Rate limits: weight 1; per-IP limits (see A2)
- Timestamp/signature requirements: none; use for clock-offset measurement
- Failure codes: -1003 (rate), HTTP 429/418
- Idempotency support: n/a (read)
- Client-order-ID restrictions: n/a
- Cancel & reconciliation behaviour: n/a
- Known testnet limitations: availability on the testnet host is implied by the doc ("Only endpoints starting with /api/*") but no page says this endpoint specifically is on testnet. Availability itself is UNVERIFIED until probed.
- Connectivity test result: **RUNTIME VERIFIED** 2026-11-13 08:04 UTC from inside the protected container through the egress proxy (profile `capability_probe`): `GET https://testnet.binance.vision/api/v3/time` -> `HTTP/1.1 200 OK`, body `{"serverTime":1794557066922}` (evidence `docs/evidence/m3a/capability-probe.json`). Unauthenticated public endpoint only.
- Unverified assumptions: endpoint exists unchanged on testnet.
- Status label: DOCUMENTED

### A1.2 Exchange info and filters: `GET /api/v3/exchangeInfo`
- Official source URLs: https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/rest-api.md ; https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/filters.md ; testnet note at https://testnet.binance.vision/
- Retrieval date: 2026-11-13
- Public or authenticated: public
- Demo vs production distinction: host; testnet symbol list, filters and `rateLimits` can differ ("not always synced with the live exchange"). Read testnet values from testnet itself, never copy production values.
- Transport: HTTP
- Auth method: none
- Required permissions: none
- Symbol/minimum-order rules (quoted from filters.md):
  - PRICE_FILTER: `minPrice`, `maxPrice`, `tickSize`; setting any to 0 disables that rule.
  - LOT_SIZE: `minQty`, `maxQty`, `stepSize`; quantity must be within min/max and a multiple of `stepSize`.
  - MIN_NOTIONAL: minimum price x quantity; `applyToMarket`; for MARKET orders uses a reference price or the volume-weighted average over `avgPriceMins` (0 means last traded price).
  - NOTIONAL: `minNotional` and `maxNotional`, with `applyMinToMarket` and `applyMaxToMarket`.
  - Which of MIN_NOTIONAL or NOTIONAL a given testnet symbol carries is UNVERIFIED (read `filters` from exchangeInfo).
  - Symbol status values: TRADING, END_OF_DAY, HALT, BREAK, CANCEL_ONLY (enums.md).
- Rate limits: weight 20; `rateLimits` array in the response defines intervals.
- Timestamp/signature requirements: none
- Failure codes: filter failures are reported as "Filter failure: PRICE_FILTER", "Filter failure: LOT_SIZE", "Filter failure: NOTIONAL", "Filter failure: MIN_NOTIONAL" (errors.md), under -1013.
- Idempotency support: n/a
- Client-order-ID restrictions: n/a
- Cancel & reconciliation behaviour: n/a
- Known testnet limitations: see A0 testnet-only settings.
- Connectivity test result: **RUNTIME VERIFIED** 2026-11-13 08:04 UTC (same method): `GET /api/v3/exchangeInfo?symbol=BTCUSDT` -> 200 on both `testnet.binance.vision` and `data-api.binance.vision`. **Observed difference:** testnet `rateLimits` ORDERS = 50 per 10 s and 160,000 per day; production market-data host ORDERS = 100 per 10 s and 200,000 per day (REQUEST_WEIGHT 6000/min and RAW_REQUESTS 300,000/5 min equal). Testnet and production order books differ (BTCUSDT best bid/ask 68706.81/68706.82 on testnet vs 68701.00/68701.01 on production at the probe time; different depth). Do not assume equal liquidity or equal filters (read filters from the testnet response at M3-B).
- Unverified assumptions: testnet `exchangeInfo` supports the same query parameters as production.
- Status label: DOCUMENTED

### A1.3 Klines: `GET /api/v3/klines` (and `/api/v3/uiKlines`)
- Official source URLs: https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/rest-api.md ; https://testnet.binance.vision/
- Retrieval date: 2026-11-13
- Public or authenticated: public
- Demo vs production distinction: host. Testnet page states klines and uiKlines "return identical data on the testnet". Whether testnet kline prices track production prices is UNVERIFIED.
- Transport: HTTP (also WS stream `<symbol>@kline_<interval>`, intervals 1s to 1M, UTC+0 or `@+08:00`)
- Auth method: none
- Required permissions: none
- Symbol/minimum-order rules: n/a
- Rate limits: weight 2 (klines and uiKlines)
- Timestamp/signature requirements: none
- Failure codes: -1003, HTTP 429/418
- Idempotency support: n/a
- Client-order-ID restrictions: n/a
- Cancel & reconciliation behaviour: n/a
- Known testnet limitations: testnet market data is not guaranteed to represent production liquidity or history (UNVERIFIED either way).
- Connectivity test result: **RUNTIME VERIFIED** 2026-11-13 08:04 UTC (same method): `GET /api/v3/klines?symbol=BTCUSDT&interval=1m&limit=2` -> 200 on testnet.
- Unverified assumptions: kline history depth on testnet; whether the testnet series is a copy of production.
- Status label: DOCUMENTED

### A1.4 Place order: `POST /api/v3/order`
- Official source URLs: https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/rest-api.md ; https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/testnet/rest-api.md
- Retrieval date: 2026-11-13
- Public or authenticated: authenticated, SIGNED, security type TRADE
- Demo vs production distinction: host and key set. Testnet keys are issued by the testnet site; keys are not interchangeable with production (inference from separate issuance, UNVERIFIED as an explicit doc statement).
- Transport: HTTP (also WS API `order.place`, not read in detail)
- Auth method: `X-MBX-APIKEY` header plus `signature` query/body parameter; HMAC-SHA-256 (hex), RSA, or Ed25519 (base64)
- Required permissions: TRADE. "By default, an API key cannot TRADE. You need to enable trading in API Management first."
- Symbol/minimum-order rules: symbol filters (A1.2) must pass.
- Rate limits: weight 1; Unfilled Order Count 1. Order count is per (sub)account, shared across IPs and keys; `DAY` interval resets at midnight UTC; exceeding returns HTTP 429 and -1015 (faqs/order_count_decrement.md).
- Timestamp/signature requirements: `timestamp` mandatory; `recvWindow` default 5000 ms, max 60000 ms, up to three decimal places; WS API page: request rejected if more than one second ahead of server time or older than `recvWindow`. Recommendation: 5000 or less.
- Failure codes: -1021, -1022, -1013, -2010, -2015, -1003, -1015, -1007 (see A3).
- Idempotency support: no explicit idempotency key beyond `newClientOrderId`. A duplicate id is rejected with "Duplicate order sent." (errors.md lists this text under -1010, -2010, -2011, -2038). On a timeout (-1007) the outcome is unknown and must be reconciled by query.
- Client-order-ID restrictions: parameter `newClientOrderId`: "A unique id among open orders. Automatically generated if not sent." "Orders with the same newClientOrderID can be accepted only when the previous one is filled" (this sentence appears in the SOR order section; whether it applies verbatim to `/api/v3/order` is UNVERIFIED). Pattern: the REST and WS API docs read did not state a regex. The FIX doc states "Client order ID fields must conform to the regex `^[a-zA-Z0-9-_]{1,36}$`" (https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/testnet/fix-api.md). Using that regex for REST is a safe design choice but is an UNVERIFIED assumption for REST.
- Cancel & reconciliation behaviour: see A1.5 to A1.8.
- Known testnet limitations: see A0.
- Connectivity test result: **UNVERIFIED / NOT TESTED** - authenticated endpoint; M3-A forbids credentials and authenticated requests, so no runtime call was made (fixture/docs only). To be verified in a separately authorised M3-B.
- Unverified assumptions: REST clientOrderId regex and length; uniqueness scope (open orders only vs all time); timeout (-1007) behaviour on testnet.
- Status label: DOCUMENTED

### A1.5 Cancel order: `DELETE /api/v3/order`
- Official source URL: https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/rest-api.md
- Retrieval date: 2026-11-13
- Public or authenticated: authenticated, TRADE
- Demo vs production distinction: host and keys
- Transport: HTTP (WS API equivalent exists, not read)
- Auth method: as A1.4
- Required permissions: TRADE
- Symbol/minimum-order rules: n/a
- Rate limits: weight 1; data source Matching Engine
- Timestamp/signature requirements: as A1.4
- Failure codes: -2011 CANCEL_REJECTED, -2013 NO_SUCH_ORDER ("Order does not exist."), -2026 ORDER_ARCHIVED, -1013, -1021, -1022
- Idempotency support: "Either `orderId` or `origClientOrderId` must be sent." Cancelling an already-terminal order is an error, not a no-op (exact code for an already-filled order UNVERIFIED).
- Client-order-ID restrictions: `origClientOrderId` selects the order
- Cancel & reconciliation behaviour: treat -2011/-2013 as "query the order to learn its true state".
- Known testnet limitations: a reset deletes all orders, so orders from before a reset return -2013.
- Connectivity test result: **UNVERIFIED / NOT TESTED** - authenticated endpoint; M3-A forbids credentials and authenticated requests, so no runtime call was made (fixture/docs only). To be verified in a separately authorised M3-B.
- Unverified assumptions: exact error for cancel-after-fill.
- Status label: DOCUMENTED

### A1.6 Query order: `GET /api/v3/order`
- Official source URL: https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/rest-api.md (offset 100000)
- Retrieval date: 2026-11-13
- Public or authenticated: authenticated, USER_DATA
- Demo vs production distinction: host and keys
- Transport: HTTP
- Auth method: as A1.4
- Required permissions: USER_DATA ("Private account information, such as order status and your trading history")
- Symbol/minimum-order rules: n/a
- Rate limits: weight 4
- Timestamp/signature requirements: as A1.4
- Failure codes: -2013 NO_SUCH_ORDER, -2026 ORDER_ARCHIVED ("canceled or expired with no executed qty over 90 days ago")
- Idempotency support: read; safe to repeat
- Client-order-ID restrictions: `symbol` mandatory; "Either `orderId` or `origClientOrderId` must be sent."
- Cancel & reconciliation behaviour: primary reconciliation call after timeouts. Status enum (enums.md): NEW, PENDING_NEW, PARTIALLY_FILLED, FILLED, CANCELED, PENDING_CANCEL (currently unused), REJECTED, EXPIRED, EXPIRED_IN_MATCH.
- Known testnet limitations: none specific documented.
- Connectivity test result: **UNVERIFIED / NOT TESTED** - authenticated endpoint; M3-A forbids credentials and authenticated requests, so no runtime call was made (fixture/docs only). To be verified in a separately authorised M3-B.
- Unverified assumptions: none beyond A0.
- Status label: DOCUMENTED

### A1.7 Open orders: `GET /api/v3/openOrders`
- Official source URL: https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/rest-api.md (offset 100000)
- Retrieval date: 2026-11-13
- Public or authenticated: authenticated, USER_DATA
- Demo vs production distinction: host and keys
- Transport: HTTP
- Auth method / permissions: as A1.6
- Symbol/minimum-order rules: n/a
- Rate limits: "6 for a single symbol; 80 when the symbol parameter is omitted". Always pass `symbol`.
- Timestamp/signature requirements: as A1.4
- Failure codes: -1003, -1021, -1022, -2015
- Idempotency support: read
- Client-order-ID restrictions: n/a
- Cancel & reconciliation behaviour: used to find stranded open orders after restart or reset.
- Known testnet limitations: none specific documented.
- Connectivity test result: **UNVERIFIED / NOT TESTED** - authenticated endpoint; M3-A forbids credentials and authenticated requests, so no runtime call was made (fixture/docs only). To be verified in a separately authorised M3-B.
- Unverified assumptions: none.
- Status label: DOCUMENTED

### A1.8 Account trade list: `GET /api/v3/myTrades`
- Official source URL: https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/rest-api.md (offset 200000)
- Retrieval date: 2026-11-13
- Public or authenticated: authenticated, USER_DATA (heading marked USER_DATA in the table of contents; the section header itself was not read, so security type UNVERIFIED beyond the TOC)
- Demo vs production distinction: host and keys
- Transport: HTTP
- Auth method / permissions: as A1.6
- Symbol/minimum-order rules: n/a
- Rate limits: weight 20 without `orderId`; 5 with `orderId`
- Timestamp/signature requirements: as A1.4; `recvWindow` max 60000
- Failure codes: standard (-1003, -1021, -1022, -2015)
- Idempotency support: read
- Client-order-ID restrictions: n/a. Params: `symbol` mandatory; `orderId`, `startTime`, `endTime`, `fromId`, `limit` (default 500, max 1000). "This can only be used in combination with symbol." "The time between startTime and endTime can't be longer than 24 hours."
- Cancel & reconciliation behaviour: fill-level reconciliation (price, qty, commission).
- Known testnet limitations: commissions on testnet are optional/custom (changelog 2026-03-05).
- Connectivity test result: **UNVERIFIED / NOT TESTED** - authenticated endpoint; M3-A forbids credentials and authenticated requests, so no runtime call was made (fixture/docs only). To be verified in a separately authorised M3-B.
- Unverified assumptions: none.
- Status label: DOCUMENTED

### A1.9 User data stream
- Official source URLs: https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/testnet/user-data-stream.md ; https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/testnet/web-socket-api.md ; https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/testnet/web-socket-streams.md
- Retrieval date: 2026-11-13
- Public or authenticated: authenticated
- Demo vs production distinction: host `ws-api.testnet.binance.vision`; the stream host `stream.testnet.binance.vision` carries market data (the `data-stream` host explicitly does not carry user data)
- Transport: WebSocket. Per the doc read: "Subscribe via the WebSocket API using an API Key" with `userDataStream.subscribe` (USER_STREAM security). The page did not mention a listenKey flow; whether a REST listenKey endpoint is still available on testnet is UNVERIFIED.
- Auth method: Ed25519 only for `session.logon`; other request signing as A1.4
- Required permissions: USER_STREAM
- Symbol/minimum-order rules: n/a
- Rate limits: WS streams host: 5 incoming messages/second (incl. PING/PONG), 1024 streams per connection, 300 connection attempts per 5 minutes per IP; connection valid 24 hours; server ping every 20 seconds, disconnect if no pong within a minute; `serverShutdown` event.
- Timestamp/signature requirements: as A1.4
- Failure codes: -2015 if the key becomes invalid mid-session
- Idempotency support: n/a
- Client-order-ID restrictions: events carry client order ID in `c`
- Cancel & reconciliation behaviour: events `executionReport` (status `X`, cumulative filled `z`, cumulative quote `Z`), `outboundAccountPosition`, `balanceUpdate`, `listStatus`, `eventStreamTerminated`. After disconnect, reconcile with REST (A1.6 to A1.8); events are not replayed (replay behaviour not stated in pages read, UNVERIFIED).
- Known testnet limitations: none specific documented.
- Connectivity test result: **UNVERIFIED / NOT TESTED** - authenticated endpoint; M3-A forbids credentials and authenticated requests, so no runtime call was made (fixture/docs only). To be verified in a separately authorised M3-B.
- Unverified assumptions: whether HMAC keys can use the user data stream (docs: only Ed25519 for `session.logon`; `userDataStream.subscribe` with a per-request `apiKey` may work with other types, not confirmed).
- Status label: DOCUMENTED

## A2. Authentication and rate-limit summary (Binance)

- Official sources: https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/testnet/rest-api.md ; https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/testnet/web-socket-api.md ; https://testnet.binance.vision/
- Header `X-MBX-APIKEY: <apiKey>`; signature parameter `signature`. Signing payload: parameters (except `signature`) joined as `key=value` with `&`; WS API page says sort alphabetically and do not percent-encode. HMAC-SHA256 hex (case-insensitive), RSA PKCS#1 v1.5 SHA-256 base64, Ed25519 base64. Docs recommend Ed25519.
- Timestamps in milliseconds by default; microseconds if `X-MBX-TIME-UNIT:MICROSECOND` is sent.
- Limits are per IP, not per key. Response headers `X-MBX-USED-WEIGHT-(intervalNum)(intervalLetter)`, `X-MBX-ORDER-COUNT-*`. HTTP 429 on breach, 418 for repeated breach (ban from 2 minutes to 3 days), `Retry-After` header. Numeric weight limits per interval are read from `exchangeInfo.rateLimits` (not quoted here; values UNVERIFIED).
- REST timeout is 10 seconds; -1007 does not always mean the order failed.
- Status: DOCUMENTED.

## A3. Binance failure codes (quoted from errors.md)

| Code | Name | Page text | Status |
|---|---|---|---|
| -1021 | INVALID_TIMESTAMP | "Timestamp for this request is outside of the recvWindow." / "Timestamp for this request was 1000ms ahead of the server's time." | DOCUMENTED |
| -1022 | INVALID_SIGNATURE | "Signature for this request is not valid." | DOCUMENTED |
| -1013 | INVALID_MESSAGE | "The request is rejected by the API. (i.e. The request didn't reach the Matching Engine.)" Used with filter failures (PRICE_FILTER, LOT_SIZE, NOTIONAL, MIN_NOTIONAL) | DOCUMENTED |
| -2010 | NEW_ORDER_REJECTED | no further detail on the page read; also carries "Duplicate order sent." | DOCUMENTED |
| -2011 | CANCEL_REJECTED | no further detail | DOCUMENTED |
| -2013 | NO_SUCH_ORDER | "Order does not exist." | DOCUMENTED |
| -2015 | REJECTED_MBX_KEY | "Invalid API-key, IP, or permissions for action." | DOCUMENTED |
| -1003 | TOO_MANY_REQUESTS | "Too many requests queued." / "Too much request weight used; current limit is %s request weight per %s." | DOCUMENTED |
| -1015 | TOO_MANY_ORDERS | "Too many orders placed without trading; current limit is %s non-trading orders per %s." | DOCUMENTED |
| -1007 | TIMEOUT | "Timeout waiting for response from backend server. Send status unknown; execution status unknown." | DOCUMENTED |
| -1100 | ILLEGAL_CHARS | "Illegal characters found in a parameter." | DOCUMENTED |
| -1102 | MANDATORY_PARAM_EMPTY_OR_MALFORMED | "A mandatory parameter was not sent, was empty/null, or malformed." | DOCUMENTED |
| -2026 | ORDER_ARCHIVED | "Order was canceled or expired with no executed qty over 90 days ago and has been archived." | DOCUMENTED |
| -1131 | n/a | not present in the content read | UNVERIFIED |

---

# Part B. OKX Demo Trading

All OKX rows: source pages could not be fetched directly (BLOCKED, ECONNREFUSED); content is from search-tool excerpts of official OKX pages. Re-read the live docs before relying on any value. Status DOCUMENTED (excerpt) unless stated.

## B0. Environment facts

| Item | Value | Source | Status |
|---|---|---|---|
| Demo REST host | Same hostname as production. Demo is selected by header `x-simulated-trading: 1` ("x-simulated-trading: 1 needs to be added to the header of the Demo Trading request"). Production requests use `0` (or no header per SDK flag: production 0, demo 1). | https://www.okx.com/docs-v5/en/ (fetch BLOCKED; excerpt via search) | DOCUMENTED (excerpt) |
| Regional REST domains | US and AU accounts (registered via app.okx.com): `us.okx.com`. EEA accounts (registration site my.okx.com): `eea.okx.com` ("please change your API request domain name to eea.okx.com instead of www.okx.com"). Global: `www.okx.com`; a 2026-05-20 changelog excerpt says "Regional domains (us.okx.com, eea.okx.com, tr.okx.com) remain unchanged" and mentions `openapi.okx.com` as a recommended global REST base with `www.okx.com` continuing to work. Exact current mapping text (including whether `app.okx.com` is a REST domain) not confirmed by a full page read. | https://www.okx.com/docs-v5/en/ ; https://www.okx.com/docs-v5/log_en/ ; https://www.okx.com/en-us/help/api-faq (all fetch BLOCKED; excerpts) | DOCUMENTED (excerpt); `app.okx.com` role UNVERIFIED; `openapi.okx.com` UNVERIFIED |
| Demo API keys | Demo keys are created while in demo trading mode inside the OKX account. FAQ excerpt: real-account calls need a real key with header 0; demo calls need a demo-account key with header 1. Steps for creating a demo key were not in the excerpts (UNVERIFIED). Third-party sources conflict on whether live keys work in demo; the official FAQ excerpt says no (error 50101). | https://www.okx.com/en-us/help/api-faq (BLOCKED; excerpt) | DOCUMENTED (excerpt) |
| Demo WebSocket hosts | Production `wss://ws.okx.com:8443/ws/v5/{public,private,business}`. Demo `wss://wspap.okx.com:8443/ws/v5/{public,private,business}`. Excerpt of the docs listed demo endpoints without the `:8443` port (`wss://wspap.okx.com/ws/v5/public` etc.), and a June 2023 change notice listed the demo business URL as `wss://wspap.okx.com:8443/ws/v5/business?brokerId=9999`. Whether `:8443` or `brokerId=9999` is required is UNVERIFIED. US site uses different hosts (`wsuspap.okx.com` for demo) per the search excerpt (UNVERIFIED in detail). | https://www.okx.com/docs-v5/en/ ; https://www.okx.com/en-us/help/changes-to-v5-api-websocket-subscription-parameter-and-url (BLOCKED; excerpts) | DOCUMENTED (excerpt), port/params UNVERIFIED |
| Demo balance top-up | "Adjust demo balance: increase is 3 requests per user per day, reduce has no limit" (excerpt; exact endpoint name not captured). | okx docs-v5 (excerpt) | UNVERIFIED (endpoint name) |

## B1. Why a shared production/demo hostname needs controls beyond host allowlisting

Facts (DOCUMENTED, excerpt): for REST, demo and production use the same hostname (`www.okx.com`, or the regional `eea.okx.com` / `us.okx.com`). The only discriminator on the wire is the request header `x-simulated-trading` (1 = demo, 0 or absent = production) combined with which API key is used. The WebSocket side does have a distinct demo host (`wspap.okx.com`).

Consequences for our platform (engineering analysis, not an OKX statement):
1. A host allowlist that permits `www.okx.com` permits both environments. It cannot tell a demo order from a real one.
2. A client bug or misconfiguration that omits the header (or sets it to `0`) sends the request to production. If a live key were present, the order would be real. The documented protection is key/environment mismatch: a demo key against production, or a live key with header 1, returns 50101 ("API key does not match current environment" per the FAQ excerpt). That protects only if only demo keys are ever present.
3. Therefore required controls beyond host allowlisting (proposals for M3-B): (a) hold only demo-created keys, never live keys, in any environment of this platform; (b) an outbound request guard that rejects any OKX REST call lacking `x-simulated-trading: 1` and rejects any call to private endpoints with a different value; (c) assert on responses: treat 50101 as a fatal configuration alarm, not a retry; (d) permissions Read+Trade only, with no Withdraw (see B2); (e) startup self-test that reads account/balance with demo header and checks it is a demo balance, before any order path is enabled; (f) WebSocket allowlist restricted to `wspap.okx.com` and never `ws.okx.com`; (g) separate secret store entries and configuration flag so the environment is a single typed value validated at boot, with a unit test that the header is always attached by the single HTTP client; (h) keep the regional domain consistent with the account region, since a mismatch is a documented source of errors.
4. Residual risk: if a user ever supplies a live key and the header guard is bypassed, only the key/environment check by OKX stands between us and a real trade. Status of the guard design: UNVERIFIED (not built or tested).

## B2. Capability sections

### B2.1 Authentication (all private REST and WS)
- Official source URL: https://www.okx.com/docs-v5/en/ (fetch BLOCKED; excerpt via search; also corroborated by https://web3.okx.com/onchainos/dev-docs/home/api-access-and-usage, which is the OKX web3 docs, not the v5 exchange docs)
- Retrieval date: 2026-11-13
- Public or authenticated: authenticated
- Demo vs production distinction: header `x-simulated-trading` and demo-created key
- Transport: HTTP and WebSocket
- Auth method: headers `OK-ACCESS-KEY`, `OK-ACCESS-SIGN`, `OK-ACCESS-TIMESTAMP`, `OK-ACCESS-PASSPHRASE` (passphrase set when the key was created). Pre-hash string: timestamp + HTTP method + request path (with query string) + body (empty for GET); HMAC-SHA256 with secret key, Base64-encoded.
- Required permissions: v5 key permissions in the excerpts: Read ("read_only") and Trade; Withdraw was not seen in the excerpts (UNVERIFIED whether it is offered for demo keys). Policy for this project: Read+Trade only; never Withdraw. FAQ excerpt: a Read-only key can read account data but cannot place orders; error 50123 appears when a key lacks trade permission (FAQ phrasing: "does not have permission to trade crypto").
- Symbol/minimum-order rules: n/a
- Rate limits: see B2.3
- Timestamp/signature requirements: REST `OK-ACCESS-TIMESTAMP` is UTC ISO format, e.g. `2020-12-08T09:08:57.715Z`, and must match the timestamp used in the signed string. WebSocket login uses Unix epoch seconds, method fixed `GET`, request path `/users/self/verify`. Allowed clock skew (commonly cited as 30 seconds) was not shown in the excerpts: UNVERIFIED.
- Failure codes: 50111 invalid key, 50112 invalid timestamp, 50113 invalid signature (these three attributed to the OKX web3 error-code page excerpt, so confirm against the v5 exchange error table); 50101 key/environment mismatch (FAQ excerpt); 50100 API frozen (HTTP 400); 50123 key lacks trade permission (FAQ excerpt). 50102, 50103, 50104, 50105, 50106, 50107, 50114: not confirmed, UNVERIFIED.
- Idempotency support: n/a
- Client-order-ID restrictions: n/a
- Cancel & reconciliation behaviour: n/a
- Known testnet/demo limitations: see B0.
- Connectivity test result: **BLOCKED** - from this PC `www.okx.com` and `ws.okx.com` resolve to 127.0.0.1 (upstream DNS sinkhole, residual R-1). Probed 2026-11-13 08:04 UTC from the protected container: the egress proxy refused `CONNECT www.okx.com:443` with reason `resolves-to-non-public:127.0.0.1` (fail closed) and the same for `ws.okx.com:443`. Not bypassed; no OKX connectivity is claimed. Public-endpoint paths and the `x-simulated-trading` behaviour remain DOCUMENTED (excerpt) / UNVERIFIED.
- Unverified assumptions: skew window; Withdraw permission availability; exact meaning of 50113 in v5 (the excerpt came from a web3 page).
- Status label: DOCUMENTED (excerpt)

### B2.2 Public data: `GET /api/v5/public/time`, `GET /api/v5/public/instruments`, `GET /api/v5/market/candles`
- Official source URLs: https://www.okx.com/docs-v5/en/ (BLOCKED; excerpt)
- Retrieval date: 2026-11-13
- Public or authenticated: public
- Demo vs production distinction: public data on the demo header is not documented in the excerpts; whether demo public data differs from production is UNVERIFIED. Demo WS public host is `wspap.okx.com`.
- Transport: HTTP (and WS public channels)
- Auth method: none
- Required permissions: none
- Symbol/minimum-order rules: `instruments` response schema includes `tickSz`, `lotSz`, `minSz` (field names confirmed in excerpt; example values blank, so numeric values are UNVERIFIED). Query with `instType=SPOT`. `instId` format for spot, for example `BTC-USDT`, is a commonly-used form but was not shown in excerpts (UNVERIFIED). Instrument `state` field exists (values such as `preopen` seen).
- Rate limits: `GET /api/v5/market/candles`: 40 requests per 2 seconds, per IP (excerpt). `public/time` and `public/instruments` limits not captured: UNVERIFIED.
- Timestamp/signature requirements: none. `public/time` existence: the excerpt could not confirm the endpoint (UNVERIFIED).
- Failure codes: 51001 "Instrument ID, Instrument ID code or Spread ID doesn't exist." (HTTP 200)
- Idempotency support: n/a
- Client-order-ID restrictions: n/a
- Cancel & reconciliation behaviour: n/a
- Known testnet/demo limitations: candle `confirm` field: 0 uncompleted, 1 completed.
- Connectivity test result: **BLOCKED** - from this PC `www.okx.com` and `ws.okx.com` resolve to 127.0.0.1 (upstream DNS sinkhole, residual R-1). Probed 2026-11-13 08:04 UTC from the protected container: the egress proxy refused `CONNECT www.okx.com:443` with reason `resolves-to-non-public:127.0.0.1` (fail closed) and the same for `ws.okx.com:443`. Not bypassed; no OKX connectivity is claimed. Public-endpoint paths and the `x-simulated-trading` behaviour remain DOCUMENTED (excerpt) / UNVERIFIED.
- Unverified assumptions: path and response of `public/time`; spot instrument numeric rules.
- Status label: DOCUMENTED (excerpt) / UNVERIFIED for items listed

### B2.3 Place order: `POST /api/v5/trade/order` (spot, `tdMode=cash`, `instType=SPOT` for instruments)
- Official source URL: https://www.okx.com/docs-v5/en/ (BLOCKED; excerpt)
- Retrieval date: 2026-11-13
- Public or authenticated: authenticated
- Demo vs production distinction: header `x-simulated-trading: 1` plus demo key
- Transport: HTTP (also WS `order` operation on the private channel, per excerpt of examples)
- Auth method: B2.1
- Required permissions: Trade
- Symbol/minimum-order rules: `tdMode` non-margin value `cash` ("cash" vs "spot_isolated" for SPOT lead trading). Limits per instrument from `tickSz` (price increment), `lotSz` (size increment), `minSz` (minimum size); exact semantics UNVERIFIED in excerpts. Excerpt noted `tradeQuoteCcy` and error 51000 for some regions: "Otherwise, the system will use the quote currency of instId as the default value, then error code 51000 will occur" (applicability by region UNVERIFIED).
- Rate limits: Place order limit NOT confirmed from excerpts (UNVERIFIED). Related (excerpt): cancel-order 60 requests per 2 seconds per User ID + Instrument ID; orders-pending 60 requests per 2 seconds (User ID rule); cancel-batch-orders 300 orders per 2 seconds; sub-account level maximum 1000 order requests per 2 seconds (new and amendment only; each batch item counted), exceeding returns 50061.
- Timestamp/signature requirements: B2.1
- Failure codes: per-order result `sCode` (0 success) and `sMsg`. 51000 "Parameter {param0} error" (HTTP 400); 51001 instrument not found; 51008 insufficient balance (shown in a WS example); 51020 TP/SL placement failure code (example in changelog). Other 51xxx codes (price/size limits, e.g. min size, tick size) were not captured: UNVERIFIED, do not hard-code.
- Idempotency support: no documented idempotency key beyond `clOrdId` (uniqueness behaviour on duplicate not captured: UNVERIFIED).
- Client-order-ID restrictions: `clOrdId` optional. "A combination of case-sensitive alphanumerics, all numbers, or all letters of up to 32 characters." Minimum length not stated (UNVERIFIED); uniqueness scope and duplicate-error code UNVERIFIED.
- Cancel & reconciliation behaviour: see B2.4 to B2.6.
- Known testnet/demo limitations: B3.
- Connectivity test result: **BLOCKED** - from this PC `www.okx.com` and `ws.okx.com` resolve to 127.0.0.1 (upstream DNS sinkhole, residual R-1). Probed 2026-11-13 08:04 UTC from the protected container: the egress proxy refused `CONNECT www.okx.com:443` with reason `resolves-to-non-public:127.0.0.1` (fail closed) and the same for `ws.okx.com:443`. Not bypassed; no OKX connectivity is claimed. Public-endpoint paths and the `x-simulated-trading` behaviour remain DOCUMENTED (excerpt) / UNVERIFIED.
- Unverified assumptions: place-order rate limit; duplicate `clOrdId` behaviour; min length.
- Status label: DOCUMENTED (excerpt) / UNVERIFIED for items listed

### B2.4 Cancel order: `POST /api/v5/trade/cancel-order`
- Official source URL: https://www.okx.com/docs-v5/en/ (BLOCKED; excerpt)
- Retrieval date: 2026-11-13
- Public or authenticated: authenticated
- Demo vs production distinction: B0
- Transport: HTTP / WS
- Auth method / permissions: B2.1 / Trade
- Symbol/minimum-order rules: n/a
- Rate limits: 60 requests per 2 seconds, per User ID + Instrument ID (excerpt)
- Timestamp/signature requirements: B2.1
- Failure codes: 51603 "Order does not exist." (HTTP 200, excerpt); WS examples show `sCode` 5XXXX placeholder "Order not exist" (not a real mapping)
- Idempotency support: not documented in excerpts (UNVERIFIED)
- Client-order-ID restrictions: cancel by `ordId` or `clOrdId` (the either-or rule was not shown in excerpts: UNVERIFIED)
- Cancel & reconciliation behaviour: confirm with order query; treat per-item `sCode` as authoritative even on HTTP 200.
- Known testnet/demo limitations: B3.
- Connectivity test result: **BLOCKED** - from this PC `www.okx.com` and `ws.okx.com` resolve to 127.0.0.1 (upstream DNS sinkhole, residual R-1). Probed 2026-11-13 08:04 UTC from the protected container: the egress proxy refused `CONNECT www.okx.com:443` with reason `resolves-to-non-public:127.0.0.1` (fail closed) and the same for `ws.okx.com:443`. Not bypassed; no OKX connectivity is claimed. Public-endpoint paths and the `x-simulated-trading` behaviour remain DOCUMENTED (excerpt) / UNVERIFIED.
- Unverified assumptions: cancel-after-fill error code.
- Status label: DOCUMENTED (excerpt)

### B2.5 Order details, pending list and fills: `GET /api/v5/trade/order`, `GET /api/v5/trade/orders-pending`, `GET /api/v5/trade/fills`
- Official source URL: https://www.okx.com/docs-v5/en/ (BLOCKED; excerpt)
- Retrieval date: 2026-11-13
- Public or authenticated: authenticated
- Demo vs production distinction: B0
- Transport: HTTP (WS private channel `orders` for lifecycle; channel name UNVERIFIED from excerpts)
- Auth method / permissions: B2.1 / Read
- Symbol/minimum-order rules: n/a
- Rate limits: orders-pending 60 requests per 2 seconds (User ID); `order` and `fills` limits not captured: UNVERIFIED
- Timestamp/signature requirements: B2.1
- Failure codes: 51603 order does not exist
- Idempotency support: read
- Client-order-ID restrictions: query by `ordId` or `clOrdId`
- Cancel & reconciliation behaviour: order states (excerpt): `live` (on book, no fills), `partially_filled`, `filled`, `canceled`, `mmp_canceled`. `filled` and `canceled` terminal; pending-order query returns only `live` and `partially_filled`; history returns `filled`, `canceled`, `mmp_canceled`. History retention windows (for example 7 days vs 3 months) were not captured: UNVERIFIED.
- Known testnet/demo limitations: B3.
- Connectivity test result: **BLOCKED** - from this PC `www.okx.com` and `ws.okx.com` resolve to 127.0.0.1 (upstream DNS sinkhole, residual R-1). Probed 2026-11-13 08:04 UTC from the protected container: the egress proxy refused `CONNECT www.okx.com:443` with reason `resolves-to-non-public:127.0.0.1` (fail closed) and the same for `ws.okx.com:443`. Not bypassed; no OKX connectivity is claimed. Public-endpoint paths and the `x-simulated-trading` behaviour remain DOCUMENTED (excerpt) / UNVERIFIED.
- Unverified assumptions: fills endpoint retention and rate limits.
- Status label: DOCUMENTED (excerpt)

### B2.6 WebSocket private channels and login
- Official source URL: https://www.okx.com/docs-v5/en/ (BLOCKED; excerpt); URL change notice https://www.okx.com/en-us/help/changes-to-v5-api-websocket-subscription-parameter-and-url (BLOCKED; excerpt)
- Retrieval date: 2026-11-13
- Public or authenticated: public channels and private channels
- Demo vs production distinction: demo host `wspap.okx.com`; demo WS needs demo-created keys
- Transport: WebSocket
- Auth method: `login` operation with key, passphrase, Unix-seconds timestamp, signature over `timestamp + "GET" + "/users/self/verify"` (Base64 HMAC-SHA256)
- Required permissions: Read for account/order channels
- Symbol/minimum-order rules: n/a
- Rate limits: connection and subscription limits not captured: UNVERIFIED
- Timestamp/signature requirements: Unix seconds for login
- Failure codes: not captured (UNVERIFIED)
- Idempotency support: n/a
- Client-order-ID restrictions: n/a
- Cancel & reconciliation behaviour: reconcile with REST after reconnect; replay behaviour not captured: UNVERIFIED
- Known testnet/demo limitations: B3.
- Connectivity test result: **BLOCKED** - from this PC `www.okx.com` and `ws.okx.com` resolve to 127.0.0.1 (upstream DNS sinkhole, residual R-1). Probed 2026-11-13 08:04 UTC from the protected container: the egress proxy refused `CONNECT www.okx.com:443` with reason `resolves-to-non-public:127.0.0.1` (fail closed) and the same for `ws.okx.com:443`. Not bypassed; no OKX connectivity is claimed. Public-endpoint paths and the `x-simulated-trading` behaviour remain DOCUMENTED (excerpt) / UNVERIFIED.
- Unverified assumptions: whether order-lifecycle events for spot arrive on `/private` or `/business` in demo, and the `brokerId=9999` requirement.
- Status label: DOCUMENTED (excerpt) / UNVERIFIED for items listed

## B3. Known OKX demo limitations

- No official page excerpt in this research enumerated demo limitations (liquidity, matching, instrument set, history retention, balance reset). All of these are UNVERIFIED.
- Third-party guides (not official, not used for facts) exist; do not rely on them.
- The demo balance can be increased with a limited-frequency request (3 per user per day per excerpt; UNVERIFIED endpoint).

---

# Binance vs OKX demo environment separation summary

| Aspect | Binance Spot Testnet | OKX Demo Trading |
|---|---|---|
| REST hostname | Separate (`testnet.binance.vision`) | Same as production (`www.okx.com` / `eea.okx.com` / `us.okx.com`) |
| REST environment selector | Host | Header `x-simulated-trading: 1` plus demo key |
| WebSocket hostnames | Separate (`stream.testnet...`, `ws-api.testnet...`) | Separate (`wspap.okx.com`; `ws.okx.com` for production) |
| Key issuance | Separate testnet site (GitHub login) | Demo-mode API key inside the main OKX account (details UNVERIFIED) |
| Wrong-environment protection | Host allowlisting suffices to prevent production reach; production keys are not issued by the testnet | Host allowlisting is insufficient for REST; relies on header guard plus key/env mismatch error 50101 |
| Reset | Periodic, about monthly, no notice, wipes orders and balances | Not documented in the excerpts (UNVERIFIED) |
| Balances | Virtual, auto-granted, reset after wipe | Demo balance; top-up limited (UNVERIFIED details) |
| Rules and limits | "Generally the same as production", read from `exchangeInfo`; not always synced | Not documented in excerpts (UNVERIFIED) |
| Client order ID | REST regex not stated; FIX regex `^[a-zA-Z0-9-_]{1,36}$`; unique among open orders | Up to 32 chars, case-sensitive alphanumerics (min length and duplicate behaviour UNVERIFIED) |
| Documentation access in this research | GitHub official repo and testnet site read; developers.binance.com blocked | All okx.com hosts blocked; search excerpts only |

---

# Open questions for M3-B

1. Re-read the live OKX docs (www.okx.com/docs-v5/en/ and the log) from a machine that can reach them, and replace the excerpt-based OKX rows. Confirm: the Demo Trading Services section verbatim; the regional domain rule and whether `app.okx.com` is a REST host; whether `openapi.okx.com` is recommended; the key-creation steps for demo keys; whether Withdraw can be enabled on demo keys.
2. Confirm the demo WebSocket URL form (`:8443` required or not; `brokerId=9999`) and which channel carries spot order events.
3. Confirm OKX place-order rate limit, order-detail and fills limits, timestamp skew window, `clOrdId` minimum length and duplicate-id error code, and the numeric tick/lot/min size values for the candidate spot symbols.
4. Binance: confirm the REST `newClientOrderId` regex and length (FIX says 1-36 of `[a-zA-Z0-9-_]`), and whether uniqueness is "among open orders" only; confirm duplicate behaviour with a runtime probe.
5. Binance: confirm whether listenKey REST endpoints exist on the testnet or only the WS API subscription; confirm which key types can use `userDataStream.subscribe` (Ed25519-only for `session.logon`).
6. Binance: read the real testnet `exchangeInfo.rateLimits`, filters (MIN_NOTIONAL vs NOTIONAL), and symbol list at runtime; record the next reset date to schedule around it; decide handling for state wiped by reset (all orders and balances).
7. Decide the order-book and price fidelity requirement: neither exchange documents demo liquidity equivalence, so fills in demo must not be used to infer production fill quality.
8. Design and test the OKX outbound guard (header enforcement, 50101 fatal alarm, WS host allowlist, demo-key-only secret store) before any private OKX call.
9. Connectivity test result fields were filled from the M3A-3 probes (see "Runtime probe results"); only public Binance testnet endpoints are RUNTIME VERIFIED, OKX is BLOCKED, every authenticated path is UNVERIFIED until a separately authorised M3-B.

---

# Runtime probe results (M3A-3, filled by the lead engineer)

Method: `infra/egress/capability_probe.py` run inside the protected container (`asta-protected-probe`, internal network, no direct route) through `asta-egress-proxy` with allowlist profile `capability_probe`; unauthenticated GET and one WebSocket upgrade only; raw evidence `docs/evidence/m3a/capability-probe.json` (2026-11-13 08:04 UTC).

| Target | Result | Status |
|---|---|---|
| `testnet.binance.vision` `/api/v3/ping`, `/time`, `/exchangeInfo`, `/depth`, `/klines` | HTTP 200 | RUNTIME VERIFIED (public) |
| `data-api.binance.vision` `/exchangeInfo`, `/depth` (production market-data host, comparison) | HTTP 200 | RUNTIME VERIFIED (public) |
| `wss://stream.testnet.binance.vision/ws/btcusdt@trade` | `101 Switching Protocols` (handshake only) | RUNTIME VERIFIED (public) |
| `wss://data-stream.binance.vision/ws/btcusdt@trade` | `101 Switching Protocols` | RUNTIME VERIFIED (public) |
| `www.okx.com` `/api/v5/public/time`, `/instruments` | proxy 403 `resolves-to-non-public:127.0.0.1` | BLOCKED (R-1) |
| `ws.okx.com` `/ws/v5/public` | proxy 403 (same reason) | BLOCKED (R-1) |
| Any authenticated Binance/OKX endpoint, `wspap.okx.com`, `ws-api.testnet.binance.vision` | not attempted (no credentials; not in either allowlist) | UNVERIFIED |

Observed Binance testnet vs production differences (runtime): order rate limits (50/10 s and 160,000/day vs 100/10 s and 200,000/day), different order-book levels and depth. Documented (not runtime-verified): monthly testnet resets with wiped balances/orders, trading keys issued via the testnet site, user-data stream via WebSocket API.

Retrieval note: `developers.binance.com` and all `okx.com` pages were unreachable from the documentation fetch tool on this PC (connection refused to 127.0.0.1:443 - the same upstream DNS sinkhole). Binance facts come from the official GitHub docs repository; **OKX facts are second-hand search excerpts of the official pages and MUST be re-read live before M3-B.**


---

# R1 reconfirmation (M3A-R1 Workstream E, 2026-11-13 ~09:15 UTC)

Re-run of the public, unauthenticated capability probe through the **gated** boundary (kernel gate + proxy, profile `capability_probe`, then restored to `runtime`): `docs/evidence/m3a/r1-capability-probe.json`.

| Capability | Status after reconfirmation |
|---|---|
| Binance Spot Testnet `GET /api/v3/ping`, `/time`, `/exchangeInfo`, `/depth`, `/klines` (public) | **RUNTIME VERIFIED** again (HTTP 200 x5); testnet `ORDERS` limits still 50/10 s and 160,000/day (production market-data host: 100/10 s, 200,000/day) |
| `wss://stream.testnet.binance.vision/ws/...` and `wss://data-stream.binance.vision/ws/...` upgrade | **RUNTIME VERIFIED** (101 Switching Protocols) |
| Binance authenticated endpoints, user-data stream, `ws-api.testnet.binance.vision` | DOCUMENTED only (official GitHub docs); not runtime verified (no credentials, not allowlisted) |
| Binance order behaviour, filters beyond `BTCUSDT`, client-id rule for REST | DOCUMENTED / UNVERIFIED (REST `newClientOrderId` pattern not stated; FIX pattern assumed) |
| OKX public REST/WS (`www.okx.com`, `ws.okx.com`) | **BLOCKED** - the gate's resolver refuses the sinkholed answer (`127.0.0.1`), so the name is never allowed; the proxy returns 403. Not bypassed. |
| OKX documentation re-read (`https://www.okx.com/docs-v5/en/`) | **BLOCKED** - documentation fetch again failed with `connect ECONNREFUSED 127.0.0.1:443` (same DNS sinkhole). OKX facts remain second-hand search excerpts (DOCUMENTED (excerpt)/UNVERIFIED) and must be re-read live before M3-B |
| OKX demo header, `50101`, regional hosts, rate limits, instrument constraints | DOCUMENTED (excerpt) / UNVERIFIED - no change |

Fixture-tested capabilities: the existing fixtures and contract tests cover only the **public** market-data parsing of both venues (candles, instruments, time); there is no fixture for any authenticated or order endpoint by design (no signing code exists), so no private capability has FIXTURE VERIFIED status.
No credentials, signed requests or order endpoints were used. The upstream DNS block was not bypassed.
