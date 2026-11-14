# ASTA-DECISION-007: offline remediation evidence (2026-11-14)

**Final status: BUILT — NOT ACCEPTED.** Credential readiness: **NO-GO.** No acceptance tag was created. Offline remediation only: no credential, authenticated request, demo/live order, external signing, host provisioning, privileged change or GitHub push occurred. Live-lock controls unchanged (`git diff fb7770b..HEAD` over `tests/live_lock`, `src/asta/risk`, `core/egress.py`, `core/environment.py`, `src/asta/security`, `src/asta/exchanges` is empty; verified again after this work).

## 1. Final commit hashes (all local, none pushed)
| Commit | Content |
|---|---|
| `dd2ec66`, `559d7ae` | governance documents of ASTA-M3B-GOVERNANCE-01 (verified, then committed locally) |
| `14b4fc9` | A: single mandatory internal gateway boundary |
| `b49ea95` | B/C/D: fill integrity, durable replay state, latched kill switch, reconciler (migration 0008) |
| the commit carrying this file | E/F: evidence, registers, decision register (`git log -1 -- docs/evidence/M3B-DECISION-007.md`) |
Baseline `origin/main` = `924da22` (unchanged remote). History preserved; nothing rewritten.

## 2. Gateway integration evidence (workstream A)
Order-path audit (`src`, `scripts`): three order-like things exist. (1) simulated orders in the research/paper engine (`research/sim/engine.py::_submit`, plain data, the only constructor of `SimOrder`); (2) paper orders are rows produced by (1); (3) `asta.orders` demo-intent processing (`Dispatcher`). Nothing in `api`, `paper`, `research_jobs`, workers, `scripts` or `exchanges` creates or submits an order or imports `asta.orders`.
* `core/order_gate.py`: one contract, surfaces SIMULATION / DEMO_INTENT / EXTERNAL; **EXTERNAL is a constant DENY** (no flag, env var or setting; module imports only `dataclasses`/`enum`, tested).
* `orders/boundary.py`: `GatedExchange` is the only way to reach a port; `Dispatcher` raises `TypeError` for a raw port; every send re-checks the gate; query/cancel on a non-simulated port are denied; an EXTERNAL port is passed to the unchanged `ExecutionGateway`, which denies everything; an undeclared port kind is refused; the latched kill switch is refused at the gate.
* The simulator calls the contract before constructing any order (AST test: the call precedes `SimOrder(`); default path unchanged (see s.8).
* Tests (`tests/orders/test_gateway_boundary.py`, 12): import-graph (nothing outside `orders` imports it; only `dispatch`/`boundary` touch a port; nothing in src/scripts builds `Dispatcher`/`GatedExchange`/`InternalGate` or implements `ExchangePort`; only the engine creates `SimOrder`; no API POST/PUT/PATCH/DELETE route mentions an order) and runtime bypass-negative (raw port, external port never called for send/query/cancel, undeclared kind, latched halt, halt arriving between check and write-ahead).
* **No external order connector was introduced.**

## 3. Fill uniqueness and overfill (workstream B; `tests/integration/test_m3b_integrity.py`, PostgreSQL)
Exact duplicate -> `DUPLICATE` no-op; conflicting duplicate -> `FillConflict` raised **and** appended to `order_fill_conflicts` (original untouched); trigger-level guards (also against raw SQL): overfill (quantity, and cumulative quote value for quote-sized orders), LIMIT price violation, fill on PENDING/REJECTED, unknown order; every refusal is on the record (`OVERFILL`, `OUT_OF_LIMIT`, `BAD_STATE`, `CONFLICT`). `record_fill` is one transaction: row lock, compare, insert, state advance (PARTIALLY_FILLED -> FILLED from the cumulative quantity; late trade after a cancel allowed within the ordered size and the CANCELED state kept). History immutable (UPDATE/DELETE/TRUNCATE refused on fills and conflicts). Concurrency (8 threads): same trade -> 1 stored / 7 duplicates; distinct 0.3 fills of a 1.0 order -> exactly 3 stored, 5 rejected, sum 0.9; conflicting reports -> 1 winner, rest recorded. 12 seeded interleavings with duplicates converge to the same ledger. Crash: a commit that dies leaves **neither** the fill nor the state change, and the retry is clean.

## 4. Durable intent replay (workstream C)
Schema unchanged. `Receiver` requires a durable `ReplayStore`; `SqlReplayStore` = one transaction (sender row lock; sequence high-water that a trigger forbids lowering/deleting; nonce table; expired nonces pruned). Results: replay refused across a simulated restart (new `Receiver` + store, same database), older sequence and reused nonce refused, 8 concurrent receivers -> exactly 1 accepts, forged/expired/oversized/wrong-sender/truncated refused without consuming state (the genuine message still accepted afterwards), `FailClosedAuthenticator` refuses everything, a non-durable store is refused at construction. Design: `docs/m3b/INTENT-CHANNEL-SECURITY.md`. No credential, key or signing implementation exists.

## 5. Integrated kill switch and reconciliation (workstream D)
Migration 0008: single latched row; trip only through `trip_kill_switch()`, release only through `release_kill_switch()` which requires the actor in `kill_switch_releasers` (**empty by default: nobody can release**, listing is a reviewed DB change, never code) and a reason of >= 10 characters; direct `UPDATE` that clears the latch is refused; events append-only; trip, release and **refused release** are appended to the audit chain (chain verified). Order lifecycle: the dispatcher gate checks the durable switch before dispatch, at the permit step, and again at the write-ahead; a DB trigger refuses `-> DISPATCHED` while latched for every worker, including a careless one that skips the gate. Cancel/flush and adoption of exchange facts remain allowed (they cannot increase exposure). `ingest_fill` halts on `FILL_CONFLICT` / `FILL_REJECTED`; `Reconciler` halts at once on an order we never made and on a serious mismatch persisting for 2 consecutive cycles (a fill landing between two reads is a lag), does nothing if the exchange is unreachable. Restart/worker-recovery test: after a halt and brand-new dispatchers, nothing is re-sent and resting orders are cancelled. **Not done:** `Trigger.RISK_HALT` has no producer: the paper/risk engines deliberately do not import `asta.orders`; a supervised bridge process would call `SqlKillSwitch.trip` (open finding R-D1, Med).

## 6. Complete mandatory security results (workstream E)
| Check | Result |
|---|---|
| Python tests, full suite (PostgreSQL 16, UTC) | **1,269 passed, 13 skipped, 0 failed** (1,282 collected) |
| Collected by area | unit 49, research 199, paper 119, live_lock 15, contracts 24, integration 143, m3a 153, orders 580 |
| ruff check / format check | clean |
| mypy (strict) | no issues in 120 source files |
| pip-audit (Python dependencies) | no known vulnerabilities |
| `pnpm audit --prod` (web) | no known vulnerabilities |
| TypeScript `tsc --noEmit`; web unit tests | clean; 48/48 |
| Browser (my Playwright mirror scripts against the restarted dev stack at migration 0008): 13-page structural check, Research (datasets/lab/results/compare incl. viewer + 390px), Audit | all pass, no overflow |
| Playwright e2e suite itself (`apps/web/e2e`, needs the Docker runner) | **BLOCKED** (Docker daemon not running) |
| Full-history gitleaks | **Executed**: gitleaks 8.30.1 (installed via Homebrew for this run), 101 commits, ~5 MB, repo config `.gitleaks.toml`: **no leaks found**; also 0 with the ignore file emptied (note: the five reviewed fingerprints in `.gitleaksignore` did not fire on this version; not removed) |
| `scripts/verify_frozen_evidence.py` | frozen evidence intact |

### Skipped / unverified classification (all 13 + others)
| Item | Count | Class | Status |
|---|---|---|---|
| `test_egress.py` x3 (compose hardening, negatives from inside the protected container, gate namespace checks) | 3 | **mandatory security gate (F-02)** | **BLOCKED**: Docker daemon not running; starting Docker Desktop and pulling `alpine:3.20`/`python:3.12-slim` and building the gate image were not authorised in this directive. Run: start Docker, `pytest tests/m3a -q` |
| `test_mac_deployment.py` nft -c x3, `test_security_host_config.py` nft -c x2 | 5 | **mandatory security gate** | **BLOCKED** (same). Evidence that is NOT equivalent but related: 22/22 namespace lab on a real aarch64 kernel (MAC-A11); not counted as these gates |
| `test_live_public.py` x3, `test_e2e_live.py` | 4 | not a security gate (public-data contract/e2e, needs `ASTA_RUN_NETWORK_TESTS=1`) | not run; needed for B-7 |
| `test_provisioning_artifacts.py` Hyper-V parse | 1 | obsolete (Windows option withdrawn) | n/a |
| Mac security-host provisioning, 3-VM boundary, reboot matrix | - | **mandatory (F-02)** | **BLOCKED**: needs `sudo` host provisioning, not authorised |
| Independent review | - | **mandatory (F-03)** | **BLOCKED**: no reviewer |
| Hosted CI run | - | mandatory (R-2) | not executed (no push) |
A simulation is not counted as an infrastructure test anywhere in this report.

## 7. M0–M2 regression
Frozen evidence intact; unit 49 + research 199 (M1) + paper 119 (M2) + live-lock 15 + contracts 24 all pass; the only production-engine change since M2 (the simulator calls the order contract; paper repeat guard default OFF) leaves behaviour identical: after the change the persisted paper sessions on the dev database replay with **900/900 checkpoint digests verified, 0 reconciliation discrepancies**, and their fills digests (`f9d8e770d765ee84`, `be76eb9ef1a917a5`, 35 fills each) are **identical to the cross-architecture drill evidence** (`docs/evidence/m3a/mac-xarch-integrity-macos-arm64.json`) recorded before this work. Accounting verification for the conditionally retained paper-engine changes (D-3): satisfied.

## 8. Governance-deviation closure register
| ID | Deviation | Closure |
|---|---|---|
| D-1 | M3-B preparatory code built and pushed without a written directive | **Ratified by DECISION-007** as an engineering baseline (offline only); recorded in the decision register; history retained |
| D-2 | pushes to `main` under standing instruction | Recorded; no further push (directive). Push path defect remains **open** (G-08: repair the `kimsudara` keychain entry, set `user.name/email`, document the path) — owner action |
| D-3 | paper-engine changes | **Conditionally retained; condition met** (s.7) |
| D-4 | additive migrations on the dev DB | Ratified; `0008` also applied to the dev DB (additive, no paper table touched, no paper session migrated) |
| D-5 | synthetic research data in the dev DB | Accepted (labelled synthetic; outside the repo tree) |
| D-6 | evidence omitted skipped tests | **Closed**: skips classified (s.6), earlier statements corrected |
| D-7 | no capability beyond offline scope | Re-verified after this work (no connector, credential, signing or network code; live-lock suite and scans pass) |

## 9. Findings status after remediation
| ID | Was | Now |
|---|---|---|
| G-01 gateway not on the path | High | **Closed (offline scope):** no dispatch without the gate, import-graph and bypass tests. Re-verify when any real port exists (it must be `EXTERNAL`, so it stays denied until the reviewed gateway change) |
| G-03 fill ledger | Med | **Closed** (DB-level) |
| G-04 channel | Med/Low | durable replay **closed**; pre-auth key-id parse and missing audience binding **open (Low)** (schema frozen); no authenticator by design |
| G-05 unwired reconciliation/kill switch | Med | **Closed within the order lifecycle**; **R-D1** open: no producer for `RISK_HALT` (supervised bridge needed) |
| G-02 vault synthetic-only not enforced | Med | Open (no workstream) |
| G-06 mandatory gates skipped | Med | **Open: 8 gates BLOCKED (Docker)** |
| G-07 gitleaks | Med | **Closed**: executed, no leaks |
| G-08 push identity | Med | Recorded; open (owner) |
| G-09 tailnet exposure | Info | Accepted |
| F-01, F-02, F-03 | **High** | **OPEN** |

**Open High/Critical:** F-01, F-02, F-03 (High). No Critical. (G-01, which was High, is closed for the offline scope.)

## 10. Recommendation
**BUILT — NOT ACCEPTED. Credential readiness: NO-GO.** Offline engineering items A-D are implemented and tested; the security-verification items that need infrastructure or people (Docker gates, host boundary, independent review, hosted CI) are BLOCKED, not passed. Before any M3-B authorisation: run the 8 Docker gates, provision and verify the boundary (F-02), obtain the independent review (F-03), decide the vault (F-05/F-06), fix the push identity (G-08), add the risk-halt bridge (R-D1), and obtain a separate CEO authorisation. No acceptance tag exists.
