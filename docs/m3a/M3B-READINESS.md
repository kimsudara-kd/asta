# M3-B readiness decision (evidence-based)

**Decision: NOT READY for credential introduction or authenticated demo trading. M3-B is not authorized and has not been started.** Prepared by the implementer; the CEO decides.

## 1. What M3-A delivered (ready)
* Infrastructure egress boundary for a protected runtime, verified from inside the container (78/78 checks, three runs, restart-persistent at container level) - `EGRESS-ENFORCEMENT.md`.
* Mock-only credential vault contract, constrained permissions, masking, audit chain, encryption-at-rest backend, immutable demo environment binding, signed-configuration verification - `CREDENTIAL-ISOLATION.md` (40 tests).
* Capability matrix with runtime-verified Binance Spot Testnet public endpoints and honest OKX BLOCKED/UNVERIFIED entries - `EXCHANGE-CAPABILITY-MATRIX.md`.
* Threat model, findings register, review package - `THREAT-MODEL.md`, `INDEPENDENT-REVIEW-PACKAGE.md`.
* Paper rejection root cause - `PAPER-REJECTION-ANALYSIS.md`.

## 2. Blockers to credential readiness (all must close before any demo credential exists)
| # | Blocker | Closure |
|---|---|---|
| B-1 | **Independent security review outstanding** (F-03) | CEO appoints an independent reviewer; written report; Critical/High closed or formally accepted |
| B-2 | Legacy M2 processes outside the boundary (F-01) | decide: the connector runs only inside the protected runtime; legacy services stay credential-free; negative tests re-run from the connector's own container |
| B-3 | Proxy egress not independently firewalled (F-02) | VM/firewall-level enforcement or reviewer acceptance with compensating controls |
| B-4 | Real vault backend and custody undefined (F-05, F-06) | select, review and test backend; reviewer key ceremony |
| B-5 | OKX connectivity/behaviour unverified (R-1, F-04) | lawful route to OKX public hosts (DNS blocker resolved outside ASTA), live re-read of OKX docs, runtime verification of header/`50101` behaviour |
| B-6 | Duplicate-order controls (F-12) | idempotent client order IDs and signal de-duplication designed and tested before any order. **Status 2026-11-14: PARTIAL** - deterministic 128-bit decision ids (`order_identity.py`, known-answer pinned, Binance/OKX format-compatible) and an in-memory exact-once gate exist with 17 tests; opt-in repeat guard exists (default OFF). **Update 2026-11-14 (2):** durable store (migration 0006: unique claim, DEMO-only CHECK, legal-transition trigger, append-only event log) and the cancel/kill-switch flush are implemented and tested on real PostgreSQL (`ORDER-PATH-DESIGN.md` s.6a/6b). **Update 2026-11-14:** the dispatch/recovery protocol (claim, guard, write-ahead, query-by-client-id recovery, same-id re-send only after consecutive NOT_FOUND, abandon/late-adopt) is implemented as pure logic and proven against a fake exchange (300 chaos seeds, mutation-checked): `docs/m3b/ORDER-PATH-DESIGN.md`, `src/asta/orders/`. NOT yet: durable UNIQUE-constraint store, wire protocol, and the exchange-behaviour assumptions U-1..U-4 (a filled id may be re-accepted: possible doubling) which need a demo key to measure. |
| B-7 | R-2 hosted CI never executed; R-11 fee/rule assumptions | at least verify demo exchange rules at runtime in M3-B |

## 3. Preconditions recommended for a future M3-B authorization
1. Venue scope: Binance Spot Testnet first (separate hosts, host allowlisting is effective, public endpoints runtime-verified); OKX only after B-5.
2. Human step outside ASTA: demo keys created by the exchange account owner with READ + SPOT TRADE only, never withdrawal/transfer, never shared in chat, never committed; handed directly to the approved vault backend. M3-A did not request or create any.
3. Allowlist change for the testnet *authenticated* host/port goes through reviewed, 2-signature configuration (`verify_privilege_config`), then a new run of all negative tests.
4. Order path additions must keep: default-deny gateway with an explicit, signed enable; per-order notional cap far below the demo balance; kill switch; client order IDs derived deterministically; reconciliation after every timeout; demo account balance reset (monthly testnet reset) handled as an expected event.
5. A fresh M3-B directive with its own acceptance gates; M3-A completion does not authorize it.

## 4. Explicit non-claims
No credential exists; no authenticated request was made; no order of any kind (demo or real) was placed; nothing in this repository can currently do so.


---

# R1 update (M3A-R1): credential readiness

**Credential readiness: NO-GO.** M3-B is not authorized and not started.

| Blocker | State after R1 |
|---|---|
| B-1 independent review (F-03) | **OPEN** - no reviewer designated; package complete and ready for one |
| B-2 legacy M2 processes outside the boundary (F-01) | **OPEN** - audited (`r1-process-audit.txt`); they stay as they are until a separately authorized, verified migration; credential-free by policy |
| B-3 boundary not independently enforced (F-02) | **OPEN, mitigated** - kernel netfilter gate built and verified (78/78 + 39/39 across restarts, fail-closed on gate loss); dedicated VM architecture, sizing and deployment plan delivered; VM not provisionable here (Windows Home, no admin, no hypervisor software); needs CEO decision on technology/approval for host changes |
| B-4 vault backend / key custody (F-05, F-06) | OPEN (unchanged; contract only) |
| B-5 OKX connectivity and behaviour (R-1, F-04) | OPEN - still BLOCKED by the upstream DNS sinkhole; reconfirmed; not bypassed |
| B-6 duplicate-order controls (F-12) | OPEN - R1 quantified the repeated-signal behaviour and proposed de-duplication boundaries (analysis only) |
| B-7 hosted CI, fee/rule assumptions | OPEN |

Closed or improved in R1: evidence protection (F-16 closed), credential hygiene (obsolete dev-login file identified, leak-checked, deletion pending owner), gitleaks exception reviewed (kept, one fingerprint), Binance Spot Testnet public capabilities reconfirmed through the hardened boundary.

Decisions requested from the CEO: (1) designate the independent security reviewer; (2) choose the dedicated boundary technology and authorize the host changes it needs (VM software/rights, virtual switch) or a separate physical firewall; (3) confirm deletion of `data/manual/creds.json`; (4) provide a lawful network path for OKX or accept Binance-testnet-only scope for M3-B.


---

# R2 update (M3A-R2): proposed Binance-only M3-B scope

**Recommendation: credential readiness remains NO-GO.** A Binance-only M3-B scope is now *proposed* and may be considered only after every security gate below closes; nothing about it is authorized, no credential exists, and demo credential creation, authenticated requests and trading stay disabled.

## R2-A. Why Binance-only is feasible as a first scope
| Aspect | Evidence | Status |
|---|---|---|
| Separate environment hosts | Official docs: REST `https://testnet.binance.vision/api`, streams `wss://stream.testnet.binance.vision`, WebSocket API `wss://ws-api.testnet.binance.vision` (GitHub docs repository; `testnet.binance.vision` page re-read 2026-11-13) - production uses different names, so **host allowlisting is an effective separation control** (unlike OKX) | DOCUMENTED + public endpoints RUNTIME VERIFIED |
| Public endpoints reachable through the hardened boundary | `ping`, `time`, `exchangeInfo`, `depth`, `klines` HTTP 200 and WebSocket upgrade 101 via the gated proxy (`r1-capability-probe.json`) | RUNTIME VERIFIED |
| Key creation | official page: "log in with your Github account and generate an API key"; RSA/Ed25519 keys are registered by public key; **"By default, an API key cannot TRADE"** - trading must be enabled in key management | DOCUMENTED (re-read 2026-11-13) |
| Virtual funds | funds are "virtual" and cannot be moved in or out of the testnet | DOCUMENTED (re-read) |
| Resets | "periodically reset to a blank state", "approximately once per month", **no prior notice** - balances, orders and possibly keys are lost | DOCUMENTED (re-read) |
| Limits and filters | official page says IP limits, order rate limits and exchange/symbol filters are "generally the same" as production; **runtime observed difference**: testnet ORDERS limits 50 per 10 s and 160,000 per day vs 100 per 10 s and 200,000 per day on the production data host - so read limits and filters from the testnet `exchangeInfo` at runtime, never assume | DOCUMENTED + RUNTIME VERIFIED difference |
| Order books | testnet and production best bid/ask and depth differ at the same instant (probe: 68706.81/68706.82 vs 68701.00/68701.01); docs do not claim equivalence | RUNTIME VERIFIED |
| Authenticated behaviour (order endpoints, weights, error codes, client-id rule, user-data stream) | DOCUMENTED in the matrix (official GitHub docs); REST `newClientOrderId` pattern not stated (FIX pattern assumed); user-data stream is a WebSocket API subscription (REST listenKey existence on testnet UNVERIFIED) | DOCUMENTED / UNVERIFIED; verification needs a demo key (M3-B) |
| Fixtures | only public market-data fixtures exist; **no authenticated fixture** by design | - |

## R2-B. Limitations and unverified items that a Binance-only scope still carries
Monthly unannounced resets; testnet liquidity not representative (fills must not be read as strategy evidence); key trading permission default-off; Ed25519/HMAC signing and clock-skew handling unimplemented (the live-lock tests forbid signing vocabulary until an authorized milestone changes them with review); REST/WS rate-limit headers not yet exercised authenticated; GitHub-account dependency for key issuance (a human step outside ASTA); allowlist for the authenticated hosts (`ws-api.testnet.binance.vision` etc.) not yet in any profile.

## R2-C. OKX stays unavailable and supported-in-design
OKX remains **BLOCKED** (upstream DNS sinkhole, R-1), not bypassed, and documentation could not be re-read. Binance-only is a **sequencing choice, not a removal of OKX**: `DEMO_BINDINGS` keeps the OKX binding, the vault keeps `OKX_DEMO`, the capability matrix and the OKX-specific controls (header guard, `50101` fatal alarm, shared-host controls) stay in the design, and OKX can be added later by a separate authorization once (1) a lawful network path exists, (2) the OKX documentation is re-read live, and (3) the shared-host demo/production separation is runtime-verified with real demo responses. No OKX code or test was removed.

## R2-D. Gates that must close before a Binance-only M3-B may be considered (all required)
1. F-03 independent review completed with credential-readiness opinion "ready" or "ready with conditions", all Critical/High closed or formally accepted.
2. F-02 dedicated security host deployed and independently verified (`VM-BOUNDARY-DESIGN.md` R2), including the restart matrix.
3. F-01 resolved by policy and test: the connector exists only inside the protected runtime; legacy processes remain credential-free.
4. F-05/F-06: vault backend and key custody reviewed; reviewer key ceremony done; signed-configuration scheme extended with expiry and replay protection.
5. F-12: idempotent client order ids and signal de-duplication designed, with tests, before any order path.
6. F-07: proxy/vault logs shipped to the audit chain with alerting.
7. A fresh CEO authorization (M3-B directive with its own gates). No demo key is to be created before that.

## R2-E. Proposed M3-B scope if all gates close (for the CEO to approve or amend)
Binance Spot Testnet only; one connector instance bound to `EXCHANGE_DEMO`/Binance; READ + SPOT_TRADE demo key, no withdrawal/transfer; hard per-order notional cap and a daily order-count cap far below the demo balance; kill switch and `RISK_HALT` integration; deterministic client order ids; reconciliation after every timeout; reset-aware bookkeeping; paper-vs-demo comparison reporting only (no performance claims); exact-once order submission verified against duplicate signals; OKX excluded until separately authorized.

> **R2 note on OKX:** the upstream DNS block lifted on 2026-11-13; OKX public endpoints answered through the boundary (see the R2 observation in `EXCHANGE-CAPABILITY-MATRIX.md`). OKX remains excluded from the proposed first M3-B scope and its authenticated/demo behaviour is still UNVERIFIED.


---

# R3 update (M3A-R3): credential readiness

**Credential readiness: NO-GO. M3-B: NOT AUTHORIZED, not started.** F-01, F-02 and F-03 are unchanged and OPEN: no dedicated infrastructure exists (single Windows 11 Home laptop, no admin rights, no hypervisor; hypervisor installation and hardware purchase are not authorized), and no independent reviewer has been designated (see `HOST-INVENTORY-AND-FEASIBILITY.md`, `REVIEWER-ENGAGEMENT.md`, `RESIDUAL-RISK-REGISTER.md`). Ready on the day infrastructure exists: reproducible seeds, hypervisor scripts, deploy/rollback, acceptance suite (controls C1..C10 with a real reboot matrix), synthetic-secret vault validation, and the database part of the M2 migration rehearsal (passed). Binance-only scope (R2) remains the proposed first M3-B scope once every gate closes; OKX stays out of the first scope.


---

# MAC-01 update: host decision

The dedicated host is the existing Apple Mac mini M4 (16 GB / 256 GB) with Linux ARM64 VMs (`MAC-MINI-DEPLOYMENT-DESIGN.md`); the x86 procurement option is withdrawn. Gates are unchanged: F-01, F-02, F-03 OPEN; **credential readiness NO-GO; M3-B not authorized**. A Binance-only first scope stays proposed (R2) and OKX stays out of it. Prepared: Phase A inventory script (not yet run on the Mac), ARM64 compatibility audit (36 aarch64 runtime wheels fetched, only dev-only `pgserver` lacks one), three-VM design with QEMU+HVF unix-socket links, rule sets, units, launchd daemons, backup/restore, POSIX supervisor, real REST/WS clients verified through the proxy. Not done: any change on the Mac, ARM64 execution of the test suite, cross-architecture replay, reboot matrix.


---

# 2026-11-14 (3) offline completion

Every offline item of the order path is now built and proven (fills ledger, amend, channel format, reconciliation, rate budget, clock guard, kill-switch triggers): `ORDER-PATH-DESIGN.md` s.7, MAC-A15. **Decision unchanged: NO-GO.** The remaining blockers need the owner (reviewer, sudo provisioning, vault custody, a testnet key, a signed directive): `docs/m3b/M3B-GATE-CHECKLIST.md`.


---

# 2026-11-14 (4) ASTA-M3B-GOVERNANCE-01 reconciliation

**Decision: NO-GO. M3-B BUILT (offline only), NOT ACCEPTED; no acceptance tag.** Full report: `docs/evidence/M3B-GOVERNANCE-01.md`. Verified: nothing in the repository can sign, authenticate or send an exchange request; live-lock controls unchanged since `fb7770b`; no key material found by supplementary history scan. Newly recorded blockers: G-01 (gateway not on the order path), G-06 (8 mandatory security tests skipped on this host), G-07 (gitleaks full-history scan not run), G-08 (push identity); D-1..D-4 await owner ratification. Updated gates: add G-A..G-F (report s.13) to R2-D.


---

# 2026-11-14 (5) ASTA-DECISION-007 remediation

**Status BUILT - NOT ACCEPTED; credential readiness NO-GO.** Offline items closed: mandatory gateway boundary, fill integrity (DB-level), durable channel replay, latched kill switch with authorised release, reconciler/fill-conflict halts, gitleaks full history (no leaks). Still blocked: the 8 Docker/firewall gates, host provisioning (F-02), independent review (F-03), hosted CI, vault backend (F-05/F-06), risk-halt bridge (R-D1), push identity (G-08). Evidence: `docs/evidence/M3B-DECISION-007.md`. Updated gate list: M3B-GOVERNANCE-01 s.13 (G-A done; G-B, G-F blocked; G-C, G-D done offline; G-E: D-1..D-4 ratified, G-08 open).


---

# 2026-11-14 (6) ASTA-M3B-R2

**Status BUILT - NOT ACCEPTED; credential readiness NO-GO.** Done: fail-closed risk-halt bridge, synthetic-only vault, channel hardening, all 8 Docker/firewall gates executed and passing, Playwright E2E 39/39 (after one UI fix), gitleaks full history clean, full regression 1,295 passed. Still blocking: F-01, F-02 (dedicated host unprovisioned, needs `sudo` and a separate authorisation), F-03 (no independent review), vault backend/key ceremony (F-05/F-06), bridge supervision (R-D1b), hosted CI, push identity (G-08). Evidence: `docs/evidence/M3B-R2.md`.


---

# 2026-11-14 (7) ASTA-M3B-R3

**Status BUILT - NOT ACCEPTED; credential readiness NO-GO.** Mac mini readiness audited (read-only), provisioning script reviewed line by line and hardened (not run), rollback prepared, 2-VM-now topology proposed for the 16 GiB host, bridge supervision made fail closed (heartbeat + monitor + units), least-privilege DB roles tested, OpenBao-transit recommended (unverified artifacts), key ceremony written and rehearsed with synthetic keys, review checklist prepared. Blocking: F-01/F-02/F-03, shared-host H-1, vault backend and ceremony, hosted CI, a separate authorisation. Evidence: `docs/evidence/M3B-R3.md`.


---

# 2026-11-14 (8) ASTA-M3B-R4

**Status BUILT - NOT ACCEPTED; credential readiness NO-GO.** Dedication of the Mac prepared (inventory with verified restorability, plan, read-only separation check; baseline FAIL 10/14), host security plan (FileVault + escrow, firewall activation, accounts, SSH, QEMU pin), two-VM capacity and load measured, bypass checks 38/38 in a scratch aarch64 kernel, vault feasibility verified against Binance/OKX/OpenBao docs (Ed25519 non-exportable only for Binance; OKX secrets can only be stored), key-replacement helper, G-10 closed, monitor events in the audit chain, off-host alert design. Urgent owner item D-0 (unrelated containers running since my R2 Docker start). Blocking: F-01/F-02/F-03, H-1, FileVault/remote-access decisions, vault rehearsals, off-host alerts, hosted CI, authorisation. Evidence: `docs/evidence/M3B-R4.md`.


---

# 2026-11-14 (9) ASTA-M3B-R5

**Status BUILT - NOT ACCEPTED; credential readiness NO-GO.** D-0 assessed: the two exposed unrelated services are **not reachable from the Internet** (outside probes refused) but are open to the LAN and tailnet; containment options and rollback prepared (none executed). Found: Docker created empty directories in the home folder at my R2 start (R4 inventory corrected); the P1 tunnel cannot start (its config file is gone); the router answers SSH on its WAN address (N-R1). All seven previously unverified PostgreSQL volumes restore logically with exact counts; 10 locally built images preserved on the external drive. Hardening runbook H1-H12 and impact matrix prepared; two-VM bypass checks 38/38, firewall persistence across guest reboots and a synthetic vault recovery rehearsal (11/11) executed in scratch; hosted CI now has mandatory security jobs and a skip guard; dead-man's-switch implemented, gaps recorded. Blocking: F-01, F-02, F-03, H-1, H-3, N-R1, host decisions, migration, authorisation. Evidence: `docs/evidence/M3B-R5.md`.
