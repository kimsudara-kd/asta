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
| B-6 | Duplicate-order controls (F-12) | idempotent client order IDs and signal de-duplication designed and tested before any order |
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
