# ASTA-M3B-R2: security and integration completion (2026-11-14)

**Final status: BUILT — NOT ACCEPTED.** Credential readiness: **NO-GO.** Baseline `2234ebf`. Offline remediation and verification only: no credential, authenticated request, external signing, order, host provisioning, privileged change, push or acceptance tag. Not an independent review and not signed as one. F-01, F-02, F-03 remain OPEN.

## 1. Risk-halt bridge (items 1-4)
Audit and design: `docs/m3b/RISK-HALT-BRIDGE.md`. Implementation `orders/risk_bridge.py` + migration `0009` (`risk_halt_bridge_seen`, append-only). Latch-only (AST test: no release call; the DB function `release_kill_switch()` with a listed releaser and a reason is the only way out), at most one trip per halt event across restarts and 8 concurrent workers (seen-row and trip in one transaction), a released halt is not re-latched by the same old event but a new halt trips again, fail closed when the paper risk state is unreadable (2 consecutive failures, no trip spam while latched) or a session is in a state the bridge does not know (drift test against `asta.paper.states`), every trip audited. 9 PostgreSQL tests. **Not done:** the bridge is not deployed or supervised as a process (R-D1b); other triggers (`DAILY_CAP_HIT`, `CLOCK_SKEW`, `RATE_LIMIT_BAN`, `EXCHANGE_RESET`, `AUTH_FAILURES`) still have no producer.

## 2. Mock vault, synthetic only (item 5)
`Vault.store` refuses, before anything is encrypted, stored, registered or logged, any value that is not `SYNTH-` + >= 16 URL-safe base64 characters (what `synthetic_secret()` produces); one bad part refuses the whole credential; key-shaped 64-character strings, empty, short, whitespace/newline-padded and look-alike prefixes are refused; the refusal is logged without the value. 3 tests; the existing vault suite and the 23-check synthetic validation still pass. The vault remains imported by no runtime module.

## 3. Intent-channel hardening (items 6-7), wire format unchanged
* No JSON is parsed before authentication (test patches `json.loads` to fail): the key id is cut from the canonical bytes with a strict bounded pattern; quotes inside values are escaped so it cannot be spoofed from a value.
* **Audience binding without a wire change:** `Receiver` now requires an explicit `allowed_key_ids` (keyword, no default, empty = nothing accepted); a key valid for another receiver is refused as `KEY_NOT_ALLOWED` before it is even handed to the authenticator.
* Authentic-but-non-canonical payloads (re-spaced, duplicate keys, mismatched key id) -> `NOT_CANONICAL`; tag length bounded 16..128 bytes.
* Default-deny preserved: `FailClosedAuthenticator` is still the only authenticator shipped and still refuses everything; non-durable replay stores are still refused.
* Residual: sequence consumption before dispatch (fail-safe), authenticator/key ceremony/transport (reviewed-design items). Closes the two Low items of G-04.

## 4. Docker / firewall gates (item 8): **executed, all passed**
Runner: this Mac mini (Apple M4, arm64), Docker Desktop 29.6.2 (aarch64 VM), started by me as a user application (no privileged provisioning; its helper was already installed). Images: `alpine:3.20` = `alpine@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc`, `python:3.12-slim`, gate image built from `infra/egress/gate`; compose project `asta-egress` (own networks; other projects' containers untouched; torn down afterwards).
| Gate | Result |
|---|---|
| `test_egress.py`: compose hardening declared | PASS |
| `test_egress.py`: negative tests from inside the protected container (>= 70 checks) | PASS |
| `test_egress.py`: kernel rules + compromised-proxy checks in the gate namespace | PASS |
| `test_mac_deployment.py`: `nft -c` of the three Mac rule sets (3) | PASS |
| `test_security_host_config.py`: `nft -c` (2) | PASS |
| `infra/egress/verify_boundary.py` (baseline, proxy restart, gate restart, gate stopped = fail closed, recovery) | `all_passed: true` (`docs/evidence/m3a/r2-boundary-verification-macos.json`) |
**Scope of this evidence (not overstated):** this is the Docker-based in-VM boundary (finding F-18: it shares Docker Desktop's kernel) running on the Mac. It is **not** the dedicated three-VM security host: provisioning (`sudo provision-host.sh --apply`) was not authorised and has not been run, so F-02 stays OPEN. The 22/22 namespace lab (MAC-A11) remains separate evidence of the VM rule sets.

## 5. Browser E2E (item 9): **39 / 39 passed after one fix**
`apps/web/e2e/run.mjs` (disposable `asta_e2e` database on the compose test PostgreSQL, system Chrome, 5 phases). First run: live 12/16, nodata 1/1, aging 1/1, research 12/12, paper 9/9. The 4 live failures were: (a) **a regression I introduced in the UI redesign** - the shell no longer showed the text `op1 (operator)` (fixed; commit `24be156`); (b) three tests needing Binance public WebSocket data: Python on this host could not verify TLS (`unable to get local issuer certificate`: the uv Python has no system CA bundle, curl/httpx work). With `SSL_CERT_FILE` pointing at certifi's bundle (environment setting only, no code or live-lock change) the live phase passed 16/16 including the Binance cross-check. Recorded as a host-configuration issue to document for operators (market-data WebSockets fail without it). The disposable database was dropped and the compose test database removed.

## 6. Full security and regression report (items 10-11)
| Check | Result |
|---|---|
| Python tests (PostgreSQL 16, UTC, boundary containers up) | **1,295 passed, 5 skipped, 0 failed**. Skips: 4 network tests (opt-in) and the obsolete Hyper-V/PowerShell parse. The 4 network tests were then run with `ASTA_RUN_NETWORK_TESTS=1`: **4 passed** |
| ruff check / format | clean (278 files) |
| mypy | no issues (121 source files) |
| pip-audit; `pnpm audit --prod` | no known vulnerabilities; no known vulnerabilities |
| `tsc --noEmit`; web unit tests | clean; 48/48 |
| gitleaks 8.30.1, full history (repo config) | no leaks (this report's commits included) |
| `scripts/secret_scan.py` | 0 findings (it initially flagged a deliberate key-shaped *test vector* I had written into `tests/orders/test_channel_hardening.py` and committed in `4a39b4a`; the test now builds it at runtime and the historical fragment is allow-listed in `scripts/secret_scan.py` with a comment - history was not rewritten; gitleaks never flagged it) |
| `scripts/verify_frozen_evidence.py` (M0-M2 manifests) | frozen evidence intact |
| Paper ledger reconciliation (`paper_integrity.py`, dev DB sessions after all changes) | 900/900 checkpoint digests verified, 0 discrepancies, fills digests `f9d8e770d765ee84` / `be76eb9ef1a917a5` = the pre-change cross-architecture evidence |
| Live-lock: `git diff fb7770b..HEAD` over `tests/live_lock`, `src/asta/risk`, `core/egress.py`, `core/environment.py`, `src/asta/exchanges` | empty (unchanged). `src/asta/security/vault.py` was changed only to **add** a refusal rule (stricter) |
**Not executed:** the 1 obsolete PowerShell test; hosted CI (no push); the dedicated security-host acceptance suite (needs the provisioned host).

## 7. High finding register (item 7)
| ID | Sev | Status | Evidence / blocker |
|---|---|---|---|
| F-01 credential-bearing processes outside an isolated runtime | High | **OPEN** | connector/vault runtime not deployed; legacy dev stack credential-free by policy and tests |
| F-02 boundary not independently enforced on dedicated infrastructure | High | **OPEN** | Docker-VM boundary gates pass (s.4) but the dedicated 3-VM host is unprovisioned (needs `sudo`); MAC-A11 lab is not deployment evidence |
| F-03 independent security review | High | **OPEN** | no reviewer; package updated, not signed |
No Critical findings. Medium items changed: G-02 vault synthetic-only **closed**; G-04 Low items **closed**; G-06 **closed** (all 8 gates executed and passed, with the scope limits above); R-D1 logic **closed**, supervision **open** (R-D1b); G-08 push identity open (owner).

## 8. Infrastructure readiness (item 8)
Selected host: Mac mini M4. Present: Docker Desktop boundary gates executable and passing; rule sets validated (`nft -c`, namespace lab); provisioning script reviewed and dry-run only. **Absent:** `_astavm` user, daemons, VMs, `socket_vmnet`, pf rules, offline backup key (private key still on host), FileVault decision. Required next, each needing separate authorisation: review and run the exact `provision-host.sh --apply` command (its effects: create the `_astavm` user and directories, install four LaunchDaemons, optional firewall flag), then the reboot matrix and C1..C10 acceptance on the provisioned host.

## 9. Independent review package
Updated (`docs/m3a/INDEPENDENT-REVIEW-PACKAGE.md` addendum): repository head, this report, DECISION-007 evidence, gate transcripts, `docs/m3b/RISK-HALT-BRIDGE.md`, `docs/m3b/INTENT-CHANNEL-SECURITY.md`. Not signed.

## 10. Recommendation
**BUILT — NOT ACCEPTED. M3-B credential readiness: NO-GO.** Every offline engineering item in this directive is done and verified, and the previously blocked Docker/firewall and browser gates now pass. NO-GO stands because F-01, F-02, F-03 are open: no dedicated host boundary, no independent review, no vault backend/key ceremony, bridge not yet supervised, hosted CI never run, and no separate authorisation. No acceptance tag exists.
