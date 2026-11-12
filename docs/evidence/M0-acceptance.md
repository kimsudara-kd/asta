# M0 acceptance evidence (ASTA-M0-IMPLEMENTATION-01 + ASTA-M0-R1 remediation)

Updated: 2026-11-12 · Repo `E:\Dev\asta`, branch `main`, **local only, not pushed**.
Raw logs: `docs/evidence/m0-r1/` · Screenshots: `docs/evidence/m0-r1/screenshots/`

## FINAL STATUS: BUILT — NOT ACCEPTED (one external blocker; otherwise acceptance-candidate quality)

Every blocker that engineering could close has been closed and re-verified. **One gate remains unmet for a reason outside engineering's control:** live verification of the OKX public feed (and the official OKX documentation) is blocked by upstream DNS filtering that was diagnosed but, correctly, not bypassed (§2).
The status becomes **ACCEPTANCE CANDIDATE** when either (a) OKX live verification is completed from a permitted network, or (b) the CEO records a decision to carry B-OKX-1/B-OKX-2 as a documented residual and make live OKX verification a precondition of M1. This document does not self-promote past that point, and nothing here is ACCEPTED.

## 1. Revised blocker register
| ID | Blocker (from the first M0 report) | Status | Evidence |
|---|---|---|---|
| B-OKX-1 | Live OKX public REST/WS unverified | **OPEN – external** | §2, `m0-r1/okx-verification.md` |
| B-OKX-2 | Official OKX docs unreadable (WS URL, keepalive, rate limits, field lists) | **OPEN – external** (partly mitigated via official SDK) | §2 |
| B-DOCKER-1 | Docker engine unstable / compose never connected | **CLOSED** | §3, `m0-r1/docker-postgres.log` |
| B-DB-1 | DB tests only on embedded PG 16.2 | **CLOSED** – full suite on compose PostgreSQL 16.4; migrations up/down/up | §3, `m0-r1/migrations-and-db-tests.log` |
| B-TYPE-1 | mypy never run | **CLOSED** – mypy strict, 52 files, 0 issues (container) | §4, `m0-r1/mypy.log` |
| B-SEC-1 | gitleaks never run | **CLOSED** – v8.30.1, history (12 commits) + tree: no leaks | §4, `m0-r1/gitleaks.log` |
| B-UI-1 | No browser/visual verification | **CLOSED** – 18 Playwright tests in system Chrome + screenshots reviewed | §5, `m0-r1/e2e-run.log` |
| B-SUP-1 | Dependency maturity unconfirmed | **CLOSED** – Next 16.4.3 (20 d), Playwright 1.64.1 (29 d), React 19.3.1 (29 d); `pnpm audit`/`pip-audit` clean | §4 |
| R-EGRESS-1 | Egress control is application-level only | **OPEN – residual, documented** | §6 |
| R-OTHER | Items in §8 | residual, documented | §8 |

## 2. OKX documentation and connectivity evidence
Full report: `docs/evidence/m0-r1/okx-verification.md`.
- **Diagnosis (read-only):** `hosts` empty; no proxy; DoH not in use; Defender only. The system resolver **and each of the 3 configured resolvers queried directly** (home router `192.168.20.1`, ISP resolvers `100.127.1.11`, `100.127.2.11`) return `127.0.0.1` for `www.okx.com`, `aws.okx.com`, `okx.com` and `api.binance.com`, while `example.com`, `github.com`, `pypi.org`, `data-api.binance.vision`, `testnet.binance.vision` resolve normally. **Source: network-level DNS sinkholing upstream of this PC (router/ISP), consistent with deliberate blocking of exchange domains.** Who or why cannot be determined from here and is not asserted.
- **Not circumvented:** no alternate DNS/DoH, VPN, proxy, hosts edit or hard-coded IP; no security setting touched.
- **Docs:** official site unreachable; read the **official OKX SDK** on GitHub (`okxapi/python-okx`): REST base, `public/time`, `public/instruments`, `market/books|candles|tickers|trades`, parameters, `x-simulated-trading` (0 live / 1 demo), WS subscribe format. **Still unconfirmed:** WS URLs, keepalive interval, rate limits, response field lists. `docs/exchange-matrix/capability-matrix.md` marks each item verified/unverified.
- **Offline verification (`tests/contracts/test_okx_offline.py`, 6 tests + fixture tests):** REST normalisation (Decimal `tickSz/lotSz/minSz`, incomplete-candle flag, order-book quote), error code 50011 and HTTP 429 surfaced not retried, clock drift (10 s ahead ⇒ not OK), and a **real local WebSocket server** exercising subscribe, text `ping`/`pong` keepalive, forced drop, reconnect with backoff, re-subscribe, replay de-duplication (3 accepted / 1 duplicate / 2 connections; initial connect not counted as a reconnect). These validate *our handling of assumed OKX shapes*, not OKX itself.
- **Binance (verified live):** time, `exchangeInfo` rules for BTC/ETH/SOL-USDT, klines, bookTicker, WS trade/bookTicker/kline streams; displayed BTC bid cross-checked in the browser test against `data-api.binance.vision` (< 1 % apart); observed clock offset 323–853 ms (limit 2 s).
- **Unblock options (owner decision, none attempted):** (1) run `ASTA_RUN_NETWORK_TESTS=1 uv run python -m pytest tests/contracts/test_live_public.py -rs` from a network where OKX access is lawful and permitted — the OKX test then runs instead of skipping; (2) supply an official docs export via an authorised channel; (3) accept the residual (CEO decision).

## 3. Docker / PostgreSQL acceptance evidence
- **Diagnosis:** at the first failure the host had commit limit ≈ 31.8 GB with ≈ 0.3 GB free and `PageFileSetting` empty; later reads show commit limit 111 GB / 78.9 GB free with system-managed pagefile active. The condition was transient; I changed no system settings. The earlier engine drops coincided with the exhausted commit state (root cause plausible, not proven). No engine drop occurred during the R1 work itself (Docker 29.6.2); the two drops happened earlier in the original M0 session.
- **Inspected before any cleanup:** ASTA containers `asta-db` (running, healthy) and `asta-db-test` (exited 255 when the engine dropped), volume `asta_asta_pgdata`, network `asta_default` — all labelled `com.docker.compose.project=asta`. Other containers (`bda-proxy`, `bosia-pms-local-postgres`) belong to other work and were not touched. Cleanup was limited to `docker compose down` (no `-v`: volume kept, nothing deleted irreversibly).
- **Compose validation:** `up -d --wait db db_test` ⇒ both healthy; `pg_isready` accepting; PostgreSQL 16.4 on both (`docker-postgres.log`). `db_test` healthcheck added.
- **Migrations on disposable `db_test` (tmpfs):** `alembic upgrade head` → 9 tables + audit triggers; `downgrade base` → only `alembic_version`; `upgrade head` again → `0001`, 9 tables.
- **DB integration tests** on that database: see §7 (all integration tests pass, incl. models == migrations and append-only audit triggers).

## 4. CI, security and static-analysis results (final code)
| Check | Result | Log |
|---|---|---|
| ruff check / format | All checks passed / 66 files formatted | `ci-security.log` |
| **mypy** 2.4.1 strict, `src` + `tests` (container; host binary blocked by App Control) | Success: no issues in 52 files. First run: 130 errors in 13 files, almost all unannotated test functions. Fixed: 1 real `src` error (CLI imported `mfa_required` via the wrong module), 1 `src` typing issue (backoff), 9 further typing issues inside tests; then `tests.*` allows unannotated test defs (bodies still checked); `src` fully strict | `mypy.log` |
| pyright 1.1.x `src` | 0 errors, 0 warnings | `ci-security.log` |
| **gitleaks** v8.30.1 | history: 12 commits, **no leaks**; working tree: **no leaks** (config excludes only untracked generated dirs; without the exclusion the tree scan flagged 216 generic-key hits all inside `.venv/…/site-packages` (208) and `apps/web/.next` (8), none in source) | `gitleaks.log` |
| custom secret scan (tree + full history incl. PNG diffs) | 0 findings (script made binary-safe in R1) | `ci-security.log` |
| pip-audit / pnpm audit | no known vulnerabilities / no known vulnerabilities | `ci-security.log` |
| Frontend | `tsc` clean; 4 unit tests pass; `next build` OK (Next 16.4.3, 7 static routes) | `ci-security.log` |
| Prohibited capabilities | no margin/leverage/borrow/withdraw/transfer/futures/order/signing/API-key code in `src/` outside the three guard modules that list those names in order to block them (`risk/gateway.py`, `core/egress.py`, `core/settings.py`); one docstring in `api/app.py` states they do **not** exist | `ci-security.log` |
| Live-lock tests | no `LIVE` env (enum + DB CHECK), gateway DENY for every environment incl. `LIVE`, env vars cannot flip it, route table has no order/trade/withdraw/transfer/borrow/margin/live/execute path and only login/mfa/logout mutate, adapters have no write/sign methods, settings refuse exchange credential variables | pytest (§7) |
| CI workflow | `.github/workflows/ci.yml` (ruff, mypy, pytest, pip-audit, secret scan, gitleaks; web typecheck/test/build/audit) — **defined but never run on a GitHub runner** (no remote; push not authorised). Every step in it has been run locally as shown above | — |

## 5. Browser acceptance results
`apps/web/e2e/` (Playwright 1.64.1 driving system Chrome; `node e2e/run.mjs` seeds a disposable `asta_e2e` DB, runs 3 phases, then stops only the processes it started, deletes the credentials file and drops the DB). **18/18 passed** in the final run on the pinned dependencies (`e2e-run.log`):

| Phase | Tests | Verified |
|---|---|---|
| live (real Binance feed + OKX as labelled fixture) | 16 | anonymous → redirected to sign-in on all 4 pages; wrong password → generic error; operator: password-only session has **no** API access (401), wrong TOTP rejected, correct TOTP signs in; viewer sign-in/out ends the session; session cookie HttpOnly + SameSite=Strict, invisible to `document.cookie`; Overview (banner `PAPER · PUBLIC MARKET DATA ONLY · NO TRADING · NO REAL FUNDS`, disclaimer, Execution DISABLED, gateway DENY, Binance "Real-time public data (read-only)", OKX "FIXTURE data (not real)", fixture clock "n/a"); Markets (3 Binance rows REAL-TIME PUBLIC DATA + fresh with decimal bid/ask, BTC bid within 1 % of Binance's own public API; 3 OKX rows FIXTURE, never REAL-TIME); Risk (DENY / M0_EXECUTION_DISABLED / live trading DISABLED); Audit (reviewer sees hash chain "verified" with `user.create`, `mfa.enroll`, `auth.password_ok`, `auth.login_failed`, `auth.mfa_failed`, `auth.mfa_ok`; no secrets in payloads; viewer/operator have no Audit link, page says role cannot view, API 403); **no interactive control on any page matches buy/sell/order/trade/withdraw/transfer/execute/leverage/margin/borrow/go-live and the only button is Sign out**; API probes (`orders`, `order`, `live`, `trade`, `execute`, `withdraw`, `transfer`, `controls/resume|halt`, `demo-runs`, `paper-runs`, `backtests`, GET and POST) all 404/405 for an authenticated reviewer; interactive API docs disabled; security headers; responsive 1280/768/390 px — no horizontal overflow, banner and nav usable on every page |
| nodata (ingest off) | 1 | all 6 rows "NO DATA" + STALE, red **RISK HALT — … STALE_MARKET_DATA** banner, Risk shows HALTED, gateway still DENY |
| aging (fixtures, 1 s stale threshold) | 1 | rows that were fresh age into **STALE** while staying labelled FIXTURE (never REAL-TIME) |

**Label policy implemented (R1):** the API's internal `LIVE` data-source value is never shown as "live". UI says "REAL-TIME PUBLIC DATA" / "Real-time public data (read-only)", every data page carries *"Real-time public prices are shown for observation only. They do not enable, authorise or imply live trading. Trading is disabled in this build."*, and a unit test forbids the bare word "live" in any data label.

**Defects found by reviewing the screenshots (assertions had passed) and fixed:** (1) the OKX fixture card said "clock offset ? ms (NOT ok)" → now "clock: n/a (fixture data)"; (2) the initial WebSocket connect was counted as a "reconnect" (Binance showed "reconnects 1" on fresh start) → only genuine reconnects count; (3) Risk text "allowed (policy only)" could be misread → "No policy breach (execution still disabled)".
Screenshots (12) in `docs/evidence/m0-r1/screenshots/`.

## 6. Application-level safeguards and the remaining infrastructure-level egress limits
**Verified application-level controls:** hard-coded host allowlist (4 Binance hosts: public REST, market-data stream, testnet REST + WS; 2 OKX hosts), TLS-only (https/wss), no URL credentials, deny-list of private/trading/funds/margin paths and private WS channels (12 denial tests, applied in both the HTTP client and the WS helper); `api.binance.com` is intentionally not allowlisted; allowlist is not configurable from the environment; exchange-credential environment variables make start-up fail; adapters expose no write/sign methods; gateway default-DENY.
**Limits (not closed):** (1) enforcement lives in our code — new code that calls `httpx`/`websockets` directly would bypass it (only the adapter packages are scanned); (2) no network-level egress policy exists (the compose file has no egress restriction, the API runs on the host, no firewall/allowlist is configured); (3) both limits are acceptable for M0 (no credentials, no order capability) but **must be closed with a deployment egress allowlist and a lint/CI rule banning direct HTTP clients outside `exchanges/` before any milestone that holds credentials (M3+)**.

## 7. Exact test counts (final code; DB = compose PostgreSQL 16.4) — `m0-r1/test-counts.log`
- **Collected: 110** = unit 49 + contracts 20 + live_lock 5 + integration 36.
- **Default run:** 106 passed, 4 skipped, 0 failed (the 4 skips = network opt-in tests).
- **`ASTA_RUN_NETWORK_TESTS=1`:** 109 passed, 1 skipped, 0 failed — the single skip is the OKX live test ("OKX unreachable from this machine"), i.e. B-OKX-1.
- **Frontend unit (node:test):** 4 passed. **Browser (Playwright/Chrome):** 18 passed. mypy/ruff/pyright/gitleaks/audits as in §4.
- Reconciliation: 106 + 4 = 110; 109 + 1 = 110.

## 8. Residual risks and limitations
1. B-OKX-1/B-OKX-2 (external; §2). 2. R-EGRESS-1 (§6). 3. The GitHub Actions workflow has not run on a real runner. 4. TOTP secrets are Fernet-encrypted with a key from `ASTA_SECRET_KEY` (env), not a vault; Argon2 defaults are OWASP (19 MiB, t=2) with enforced floors; tests use the 7 MiB/t=5 OWASP profile. 5. Account lockout (5 failures/15 min) can lock out the single operator; no per-IP throttling. 6. Market state is in memory; symbol rules are fetched/tested but not persisted or displayed; `incidents` are read-only and nothing writes them yet; no halt/resume control (outside the M0 scope list). 7. `/v1/audit` re-verifies the whole chain per call (fine at M0 volume). 8. Embedded Postgres (`pgserver`, dev-only dependency) is still the default DB for `pytest` when `ASTA_TEST_DATABASE_URL` is unset. 9. Docker on this PC dropped twice earlier in the session during memory pressure; the compose stack is validated but the host's resilience is not guaranteed. 10. Two ASTA compose containers are left running (`asta-db` :5640, `asta-db-test` :5641) — stop with `docker compose -f infra/docker-compose.yml --profile test stop`. 11. Playwright drives the system Chrome; a different browser/version is not covered. 12. Browser tests use fixture OKX data by design.

## 9. Code changes and commit hashes
Baseline `8f0ae35`. R1 commits (all local): `1d3f03b` types (mypy-clean `src`+`tests`, CLI import fix, backoff typing), compose `db_test` healthcheck, Docker/PG logs · `b526355` `.gitleaks.toml` · `3151d19` offline OKX tests (REST, clock, real local WS reconnect/keepalive/dedupe) · `6f96602` Playwright acceptance suite + seed/orchestrator, unambiguous data-source labels + disclaimers, fixture clock "n/a", reconnect-count fix, Risk wording, OKX blocker report, capability-matrix update · `5ae4d90` frontend pins ≥14 days old, binary-safe secret scan, tsbuildinfo untracked, refreshed logs · the commit containing this file (see `git log`).
No change weakened any safety or security control; the only test-side relaxation is the `tests.*` mypy override described in §4.

## 10. Final M0 acceptance recommendation
**Do not mark M0 ACCEPTED. Recommend ACCEPTANCE CANDIDATE status once the OKX residual is resolved by one of the two routes in §2.** All other gates are met with evidence: no execution path / no `LIVE` / no credentials; Decimal/numeric, auth/MFA/RBAC, migrations (PG 16.4), recovery/stale/duplicate/clock tests; security scans clean; browser acceptance 18/18 with honest source labelling. Do not begin M1 without separate authorization; before M3, close R-EGRESS-1.
