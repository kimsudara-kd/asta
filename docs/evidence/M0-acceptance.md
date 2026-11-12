# M0 acceptance evidence (ASTA-M0-IMPLEMENTATION-01)

Date: 2026-11-12 · Repo: `E:\Dev\asta` (branch `main`, local only, **not pushed**)
Commits: `6e1adbb`, `746e690`, `9e8b254`, `00b5c08`, `5335145`, `34382d6` (HEAD at time of the test runs below; this evidence file is committed after).

**STATUS: BUILT — NOT ACCEPTED** (reasons in §7). Not claimed as an acceptance candidate.

## 1. Architecture summary
Modular monolith: Python 3.12 / FastAPI / SQLAlchemy 2 + Alembic / PostgreSQL / Pydantic v2 / Decimal; Next.js (TypeScript)
dashboard proxying `/api`. ADRs 0001–0004 in `docs/adr/`. Redis deferred. Diagram: `docs/architecture/system-overview.md`.

## 2. Module inventory (`src/asta/`)
| Module | Purpose |
|---|---|
| `core` | `Environment` (BACKTEST/PAPER/EXCHANGE_DEMO only), Decimal helpers, UTC/clock-drift, egress allowlist, canonical events, settings (refuses exchange-credential env vars) |
| `exchanges/{common,binance,okx}` | PUBLIC market-data adapters: server time, symbol rules, candles, best quote, WS stream with reconnect/backoff; no auth, no write methods |
| `market_data` | dedupe LRU, freshness monitor (unknown = stale), reconnect gap marking, per-venue ingest supervisor, labelled FIXTURE feed |
| `risk` | `ExecutionGateway` (default DENY; every request denied in M0), `RiskPolicy`/`evaluate` (handoff §8 caps and trips, fail-closed) |
| `audit` + `db/audit_store` | SHA-256 hash-chained, DB-trigger append-only audit log |
| `auth` | Argon2id, TOTP MFA (secret Fernet-encrypted), hashed opaque sessions, lockout, RBAC (viewer/operator/reviewer/admin) |
| `api` | FastAPI app, security headers, CSRF on logout |
| `db` | models + session |
| `cli.py`, `main.py` | create-user bootstrap, server entry (127.0.0.1 only) |

## 3. API inventory
Public: `GET /health`, `GET /readiness`.
Auth: `POST /v1/auth/login`, `POST /v1/auth/mfa`, `GET /v1/auth/me`, `POST /v1/auth/logout` (CSRF header).
Read-only (viewer+): `GET /v1/overview`, `/v1/venues`, `/v1/markets`, `/v1/risk`, `/v1/incidents`. Reviewer/admin: `GET /v1/audit`.
Not implemented (outside M0 scope list): backtest/paper/demo runs, halt/resume, reconciliation.
A test asserts the only mutating routes are login/mfa/logout and no path contains order/trade/withdraw/transfer/borrow/margin/live/execute.

## 4. Database schema inventory (migration `0001`)
`users`, `sessions`, `exchange_connections` (metadata + secret *reference* only; CHECK env ∈ 3 values), `markets`,
`market_rules_versions`, `risk_policies`, `incidents`, `audit_events` (trigger forbids UPDATE/DELETE/TRUNCATE; CHECK env).
Money columns `NUMERIC(38,18)`, times `timestamptz`. Test asserts models == migrations (alembic autogenerate diff empty) and downgrade/upgrade round-trips.
Remaining handoff §6 tables (orders, fills, ledger, backtests…) are deliberately deferred to the milestones that need them.

## 5. Binance / OKX capability matrix
`docs/exchange-matrix/capability-matrix.md` (retrieved 2026-11-12). Binance: verified against official GitHub docs. **OKX: NOT verified against official docs** (unreachable).

## 6. Safety-control verification
| Control | Evidence |
|---|---|
| No `LIVE` environment | `test_environment_members_exact_and_no_live`; AST test finds no `LIVE` definition; DB CHECK rejects `'LIVE'` (conn + audit tables) |
| Gateway default-DENY | `test_gateway_denies_everything_in_m0` over BACKTEST/PAPER/EXCHANGE_DEMO/LIVE/""/"paper"; env vars `LIVE_TRADING_ENABLED`/`ASTA_LIVE_TRADING_ENABLED` do not change it |
| No order/transfer/borrow/margin code | source-scan test over `src/asta/exchanges`; runtime `dir()` test of both adapters; no HMAC/signing/API-key header strings outside the guard files |
| No credentials | `Settings` refuses `BINANCE_*/OKX_*_API_KEY/_SECRET/_PASSPHRASE`; `exchange_connections` stores no secrets; secret scan of tree + full history: 0 findings |
| Egress allowlist | 4 allowed + 12 denied-URL tests (http, unlisted host, `/api/v3/order`, `/sapi/`, `/api/v5/trade|asset|account`, private WS, URL credentials); HTTP client and WS helper both call it; `api.binance.com` is intentionally not allowlisted |
| Boundary | import-graph test per module; only `risk/gateway.py` may name the execution constant (the API reads it via read-only accessors — this test caught and forced a fix) |
| No order routes | route-table test (see §3) |
| Real run | live API process: `gateway_probe = DENY / M0_EXECUTION_DISABLED`, `execution_enabled=false`, `live_trading_enabled=false` |
| Zero orders | no code path exists to submit one; no authenticated exchange call exists |

## 7. Gate assessment
| Gate | Result | Evidence / caveat |
|---|---|---|
| Both public adapters operate correctly | **PARTIAL** | Binance: REST + WS verified live (`test_binance_*_live`, curl of running API showed LIVE prices). **OKX: parsing verified on fixtures only; live OKX unreachable from this PC** (`www.okx.com` resolves to 127.0.0.1) — not bypassed |
| Execution paths disabled / no LIVE | met | §6 |
| Financial numeric tests | met | Decimal/float-rejection, floor-to-step property test, NUMERIC round-trip |
| Auth, MFA, RBAC | met | `tests/integration/test_auth.py`, `test_api.py` |
| Migrations | met (on PG 16.2 embedded) | compose Postgres 16.4 connection not exercised (§8) |
| Recovery / stale-data | met at unit level | fake-adapter reconnect-replay dedupe test; stale/unknown→stale; venue-failure isolation. No real network-drop drill |
| Duplicate-event protection | met | `test_duplicate_events_dropped`, replay-after-reconnect |
| Clock drift | met | unit tests (ok / exceeded / negative / unknown=fail-closed); live Binance offset observed 853 ms (< 2 s limit) |
| Security scanning | **PARTIAL** | `pip-audit`: none; `pnpm audit`: none; secret scan 0 findings. **mypy could not run locally** (blocked by Windows App Control; pyright 1.1.414 on `src`: 0 errors); **gitleaks not run locally** (configured in CI, CI not executed) |
| Dashboard shows real data or labels fixture | met (API + served HTML) | real process: Binance rows `LIVE`, OKX rows `FIXTURE`; web server served `/login`, `/api` proxy login/overview. **No browser/visual UI test was run** |
| Zero real orders | met | §6 |
| No secrets in git history/logs | met (scanner) | custom scanner; logs not otherwise audited |

## 8. Commands run and results (2026-11-12, this machine)
```
uv run python -m pytest tests -q                         -> 100 passed, 4 skipped
ASTA_RUN_NETWORK_TESTS=1 uv run python -m pytest tests   -> 103 passed, 1 skipped (OKX unreachable)
  by dir: unit 49 passed · contracts 11 passed/3 skipped (default) · live_lock 5 passed · integration 35 passed/1 skipped
uv run ruff check . ; uv run ruff format --check .       -> All checks passed ; 60 files already formatted
pnpm dlx pyright --pythonpath .venv/Scripts/python.exe src -> 0 errors, 0 warnings
uv run python -m pip_audit                               -> No known vulnerabilities found
pnpm audit (apps/web)                                    -> No known vulnerabilities found
uv run python scripts/secret_scan.py                     -> secret scan: 0 finding(s)
pnpm run typecheck / test / build (apps/web)             -> clean / 3 passed / build OK (7 static routes)
docker compose -f infra/docker-compose.yml --profile test config -q -> valid
```
Real-process run: embedded Postgres → `alembic upgrade head` → API via `python -m asta.main` → `curl` login + `/v1/markets`
(BINANCE BTC/ETH/SOL `LIVE fresh`, OKX `FIXTURE`) → `next start` + proxy checks. All processes I started were stopped.

## 9. Unresolved defects and limitations
1. **OKX live access** blocked locally (DNS to 127.0.0.1 for `www.okx.com`, also `api.binance.com`); OKX official docs not readable. OKX endpoints/fields are unverified; **owner must resolve reachability** (legitimately, no circumvention of regional restrictions).
2. **Docker engine on this PC is unstable** (500 / pipe missing; host has ~no free commit memory, no pagefile). Compose file validated by `config` only; containers `asta-db`, `asta-db-test` and volume `asta_asta_pgdata` were created and could not be inspected/removed after the engine dropped — clean up with `docker compose -f infra/docker-compose.yml --profile test down -v` when Docker is healthy.
3. DB tests use embedded PostgreSQL 16.2 (dev-only `pgserver`) here; CI uses `postgres:16.4`.
4. **mypy not run locally** (App Control); CI step exists but has never run. gitleaks/CI never executed (no remote, no push).
5. Egress control is application-level (hard-coded allowlist). A deployment/network-level egress policy does not exist yet, and new code that uses `httpx`/`websockets` directly would bypass it (only the adapters are scanned).
6. TOTP secrets are Fernet-encrypted with a key from `ASTA_SECRET_KEY` (env), not a vault. Argon2 defaults (19 MiB, t=2) with a configurable OWASP-floor; tests use the 7 MiB/t=5 profile.
7. Account lockout (5 failures, 15 min) can be used to lock out the single operator; no per-IP throttling yet.
8. Market state is in memory; symbol rules are fetched/tested but not persisted or displayed; `incidents` are read but nothing writes them yet; no halt/resume control.
9. Frontend tests are minimal (3 unit tests); no Playwright/e2e or visual verification.
10. `/v1/audit` verifies the entire chain on each call (fine at M0 volume, not scalable).
11. Dependency versions (Next 16.4.6, React 19.3.1) were taken as current without confirming they are ≥ 2 weeks old.

## 10. M1 readiness recommendation
**Not yet.** Before M1 authorization: (a) resolve OKX reachability and re-run the OKX live + docs verification, (b) confirm compose stack and CI (incl. mypy, gitleaks) on a healthy runner, (c) CEO/reviewer review of §9 items 5–7. Do not begin M1 without separate authorization.
