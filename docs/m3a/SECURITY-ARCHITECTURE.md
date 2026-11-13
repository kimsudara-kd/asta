# M3-A security architecture, baseline audit, plan and acceptance matrix

Baseline: tag `m2-accepted-with-residual` = `fe8194c` (verified at start: `git rev-parse m2-accepted-with-residual^{}` = `fe8194c`; the working tree was clean). M3-B is NOT authorized and not started.

## 1. M3A-0 read-only baseline audit (performed before any implementation)

### 1.1 Accepted evidence and open residuals read
M0/M1/M2 acceptance records and `docs/decisions/ceo-approval-register.md`. Open residuals carried into M3-A: R-1 OKX connectivity (blocked by upstream DNS), R-2 hosted CI never run, **R-3 infrastructure egress not enforced (mandatory before any credential)**, R-11 fee/rule assumptions, 24/7 reliability unverified, the unexplained `0xC000013A` relaunch, 29 noncritical pyright findings outside `src`.

### 1.2 Outbound clients inventory (source)
| Client | Where | Control |
|---|---|---|
| `httpx.AsyncClient` (REST) | `exchanges/common/http.py` (`PublicHttp`), used by the Binance and OKX adapters | `assert_url_allowed` (https only, host allowlist, forbidden private/trading path fragments, no URL credentials); `follow_redirects` off; `trust_env` default (would honour `HTTP(S)_PROXY` env - finding F-11) |
| `websockets.connect` | `exchanges/common/ws.py` (Binance kline stream; OKX WS not used for candles) | same URL guard before connect; redirect handling by the library not verified (F-10) |
| PostgreSQL (pg8000) | API, services, tests | local `127.0.0.1:5640` / `5641` only |
| Supervisor subprocesses | `scripts/paper_supervisor.py` (`tasklist`, `powershell`, `taskkill`, python child) | local only, fixed argv |
| Research package | `research/` | pure; live-lock test forbids network/DB imports |
| No other outbound code | grep of `src/` for `httpx|websockets|requests|urllib|aiohttp|socket` | only the two files above |

### 1.3 Processes with network access (this PC, ASTA)
Windows host (not containerised): `paper_supervisor` -> `paper_service` (outbound HTTPS/WSS to Binance and OKX public hosts; DB on localhost); the API/web servers when started (listen on localhost). Docker: `asta-db` (127.0.0.1:5640), `asta-db-test` (127.0.0.1:5641, test profile); other projects' containers (`pethhub-postgres` published on 0.0.0.0:5440, `bda-proxy`, internal networks `bda-internal*`, `bosia-pms-local-net`, `pethhub_default`) - read for isolation purposes only, never modified or contacted.

### 1.4 Windows / Docker / WSL / proxy / DNS observations
* Windows Firewall: all three profiles enabled, `DefaultOutboundAction = NotConfigured` (outbound allowed). WinHTTP proxy: direct. No `*_PROXY` environment variables.
* DNS: Wi-Fi uses ISP resolvers plus 8.8.8.8/8.8.4.4. From this PC `www.okx.com`, `ws.okx.com`, `developers.binance.com` and other `okx.com` hosts resolve to `127.0.0.1` (upstream sinkhole, R-1); the same effect made the documentation fetch tool fail with `ECONNREFUSED 127.0.0.1:443`.
* Docker Desktop 29.6.2 (WSL2 backend, cgroup v2, no per-container firewall UI); WSL distros: `docker-desktop` running, `Ubuntu-24.04` stopped (not started, belongs to other work).
* Existing project networks (`bda-internal` is internal) show the isolation pattern already used by another project; ASTA created its own.

### 1.5 Existing application controls
`core/egress.py` allowlist and forbidden path fragments; `risk/gateway.py` ExecutionGateway default DENY with `EXECUTION_ENABLED_IN_THIS_BUILD = False` referenced only there; `Environment` without LIVE; settings refuse exchange-credential env names; 15 live-lock tests (no credential/signing vocabulary, import boundaries, gateway DENY, route allow-list); Argon2id (OWASP parameters, floor enforced), TOTP MFA required for operator/reviewer/admin, lockout, opaque DB sessions with CSRF tokens (30 min TTL), static RBAC (viewer/operator/reviewer/admin, deny by default; no role can place orders), tamper-evident hash-chained audit log (`audit_events`, verified in M2). Secret handling before M3-A: no secrets besides dev-only DB passwords in `infra/docker-compose.yml` and the supervisor launcher (documented dev values), and a local, git-ignored, plaintext file of seeded app-user logins in `data/manual/creds.json` (finding F-08).

### 1.5.1 Controls that could affect other projects (and what was done)
Host firewall rules, Docker daemon/network defaults, DNS/proxy settings, scheduled tasks, shared containers/networks. **None were modified.** All M3-A infrastructure is namespaced `asta-*` / `asta_*`, publishes no ports and uses its own internal network.

## 2. Target architecture

```
 Windows host (legacy, unchanged)                Protected execution boundary (new)
 ------------------------------------            ----------------------------------------------------
 supervisor -> paper_service  ---public data---> Internet           protected runtime (future connector)
 (app-level allowlist only)                                              |  internal net, no route out
                                                                         v
                                                                   asta-egress-proxy (default-deny CONNECT)
                                                                         |  allowlisted public names only
 ExecutionGateway: default DENY (unchanged)                              v  Internet
 security/ (vault contract, binding, signed config)  [mock only, not wired]
```

Layers (each independently effective): (1) infrastructure: internal network + proxy policy; (2) application: egress allowlist/path guard, gateway DENY, import boundaries; (3) identity: vault contract + connector principal + signed config; (4) environment: immutable demo binding; (5) detection: proxy decision log, audit chain; (6) absence: no credentials, no signing code, no authenticated client exist.

## 3. Implementation plan and status
| Stage | Scope | Status |
|---|---|---|
| M3A-0 | read-only audit, plan, matrix | done (this document) |
| M3A-1 | internal network + allowlist proxy + negative tests (`infra/egress`, `tests/m3a/test_egress.py`) | done, 78/78 runtime checks, 3 runs |
| M3A-2 | mock-only vault, redaction, binding, signed-config contracts (`src/asta/security`) | done, 40 tests |
| M3A-3 | official-doc capability matrix + public runtime probes | done; Binance testnet public RUNTIME VERIFIED, OKX BLOCKED/UNVERIFIED |
| M3A-4 | threat model, internal negative tests, independent review package | done internally; **independent review outstanding** |
| M3A-5 | full regression, final verification, acceptance document | see `docs/evidence/M3A-acceptance.md` |
| Workstream F | read-only paper rejection analysis | done (`PAPER-REJECTION-ANALYSIS.md`) |

## 4. Acceptance matrix (mandatory gates, section 10 of the directive)
| # | Gate | Verdict | Evidence / note |
|---|---|---|---|
| 1 | Egress isolation enforced outside application logic | **MET for the protected runtime; NOT applied to legacy M2 host processes** | EGRESS-ENFORCEMENT.md section 7 (F-01) |
| 2 | No production trade destination reachable from the protected runtime | MET | negative tests N01/N02 (production hosts refused, direct fails); OKX shared host caveat F-04 |
| 3 | Direct-IP, redirect, proxy and IPv6 access blocked | MET | N04, N06, N09, N10 |
| 4 | Mock credential values never exposed | MET | `test_security.py` masking/logging/crash-report/audit tests |
| 5 | Future credential permissions explicitly constrained | MET (contract) | READ/SPOT_TRADE only, withdrawal/transfer/margin refused |
| 6 | No real exchange keys exist | MET | no `.env*`, no key-shaped literals, secret scan 0, gitleaks history clean; F-08 is app-user dev logins, not exchange keys |
| 7 | No authenticated exchange orders possible | MET | no signing/authenticated client code; live-lock tests; vault not wired; connector absent |
| 8 | Original ExecutionGateway remains default DENY | MET | `git diff fe8194c -- src/asta/risk src/asta/core` empty (see acceptance doc) |
| 9 | M0/M1/M2 safety regressions pass | MET | full suite (acceptance doc) |
| 10 | Paper sessions and accepted evidence intact | MET | sessions RUNNING, reconcile 0 discrepancies, M0/M1/M2 evidence diff empty |
| 11 | Binance documentation and capabilities recorded | MET | EXCHANGE-CAPABILITY-MATRIX.md part A + runtime probes |
| 12 | OKX limitations and regional access accurately recorded | MET with stated uncertainty | part B; **OKX facts are search-excerpt DOCUMENTED, regional hosts partly UNVERIFIED, runtime BLOCKED** |
| 13 | Testnet/demo vs production differences documented | MET | matrix; runtime-observed rate-limit and order-book differences |
| 14 | No unresolved critical security findings | MET (none rated Critical) | THREAT-MODEL.md findings register (High items open) |
| 15 | Incomplete independent review explicitly a blocker to credential readiness | **Recorded as BLOCKER** | INDEPENDENT-REVIEW-PACKAGE.md, M3B-READINESS.md |
| 16 | Security tests reproducible | MET | commands in acceptance doc; JSON evidence under `docs/evidence/m3a/` |
| 17 | No other project disrupted | MET (as observable) | Docker before/after diff shows only ASTA additions; no host/firewall/DNS change |
