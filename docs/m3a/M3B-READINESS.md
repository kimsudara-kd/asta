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
