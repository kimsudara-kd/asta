# ADR-0002: Environment enum and layered live lock

Status: accepted (M0)

**Decision.**
1. `core.environment.Environment` has exactly `BACKTEST`, `PAPER`, `EXCHANGE_DEMO`. There is **no** `LIVE` member; a test asserts the member set and that `Environment("LIVE")` raises.
2. Exchange adapters in M0 expose **public market-data methods only**. There is no `place_*`/`cancel_*`/`withdraw`/`transfer`/`borrow` symbol anywhere in `src/asta/exchanges`; a source-scan test and a runtime attribute test enforce this.
3. Outbound HTTP/WS goes through `core.egress.assert_url_allowed`, a hard-coded allowlist of public market-data hosts (not configurable from environment variables). Any other host, any non-HTTPS/WSS scheme, and any path under `/api/v3/order`, `/sapi/`, `/api/v5/trade`, `/api/v5/asset`, `/api/v5/account` (and similar private/trading paths) is refused.
4. `risk.gateway.ExecutionGateway.authorize()` is default-DENY. In M0 it returns DENY for every request (reason `M0_EXECUTION_DISABLED`); it cannot be enabled by environment variable. An import-graph test ensures only `risk.gateway`/future `orders` may be the path to adapter write methods (which do not exist).
5. No API route that places or cancels orders is registered; a test walks the OpenAPI/route table.
6. Authenticated exchange access (API keys) is not implemented. Settings reject any `*_API_KEY`/`*_SECRET` env var being set (fail-closed at start-up).
