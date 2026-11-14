# Residual-risk register (M3A-R3, status 2026-11-13; M3B-GOVERNANCE-01 update 2026-11-14 appended below)

Severity: Critical (credentials/live trading exposed now), High (blocks credential readiness), Medium, Low, Info. "Owner" is who must act; "Closure" is the evidence that closes it. No finding is Critical. **Mandatory gates F-01, F-02, F-03 are High and OPEN.**

| ID | Sev | Risk | Current mitigation | Owner | Closure condition | Status |
|---|---|---|---|---|---|---|
| F-01 | High | Credential-bearing processes might run outside an isolated runtime; legacy Windows processes have egress and no boundary | legacy processes credential-free by tested policy (`test_legacy_isolation.py`, settings refusal, import boundaries); credential design places vault and connector only on `asta-rt`; nothing credential-bearing exists | CEO + Engineering | dedicated runtime deployed; C1/C6 executed (no route from legacy hosts); reviewer accepts | **OPEN** |
| F-02 | High | No independently enforced boundary on dedicated infrastructure | in-Docker kernel gate verified (78/78, 39/39, restarts, fail-closed); dedicated-host design, rule sets (`nft -c`), seeds, hypervisor scripts, deploy/rollback, acceptance suite ready | CEO (hardware/authorization) | C1..C10 executed on dedicated host incl. real reboot matrix; reviewer verifies | **OPEN** (no dedicated infrastructure; Option A recommended) |
| F-03 | High | No independent security review | package, index, engagement kit, manifests | CEO (designate reviewer) | signed report; Critical/High closed or accepted | **OPEN** |
| F-04 | Med | OKX demo/production share a host; header-based separation unverified | OKX excluded from first scope; controls designed; public OKX endpoints now observed reachable (single probe) | Eng | live docs re-read + runtime verification with a real demo key in an authorized M3-B | Open |
| F-05 | Med | Vault is an in-process contract; real backend/custody undefined | placement design (separate users, `PrivateNetwork` vault, unix socket, KEK as encrypted credential), 23-check synthetic validation | Eng + reviewer | backend chosen and reviewed; validation run on the runtime VM | Open |
| F-06 | Med | Reviewer key set and ceremony for signed privilege configs undefined; no expiry/replay protection | 2-of-N Ed25519 verification implemented and tested | CEO/Eng | named reviewers, key ceremony, expiry/replay fields | Open |
| F-07 | Med | Proxy/vault logs not shipped to the audit chain; no alerting; no drift detector | monitoring plan (R2-4) | Eng | implemented and tested | Open |
| F-09 | Med | Malicious *public* DNS answer for an allowed name accepted | non-public answers refused; client TLS validation | Eng + reviewer | pinning decision | Open |
| F-12 | Med | Duplicate/runaway orders once an order path exists (strategies re-emit rejected intents ~96 %) | quantified; D1+D2 implemented as an opt-in guard (default OFF, `repeat_guard.py`); D5 deterministic ids + exact-once gate implemented and tested as a pure module (`paper/order_identity.py`, not wired anywhere); D4 and the DB-backed gate are M3-B work | Eng | still needed: UNIQUE-constraint-backed gate in the order path, query-by-client-id reconciliation, exact-once test against duplicate signals with a real connector | Open (partially mitigated, no order path exists)|
| F-18 | Med | Docker gate container holds `NET_ADMIN` on the shared Docker Desktop kernel | confined to its namespace, hardened | Eng | superseded by dedicated host | Open (part of F-02) |
| F-10 | Low | `websockets` redirect behaviour unverified | proxy policy still refuses unauthorized targets | Eng | test/pin behaviour | Open |
| F-11 | Low | `httpx` `trust_env` honours proxy variables in legacy processes | none credential-bearing | Eng | connector built with `trust_env=False` | Open |
| F-13 | Low | Base images pulled by tag, not digest | pinned minor tags | Eng | digest pinning | Open |
| F-14 | Low | Docker Desktop/VM restart and Windows reboot not exercised; dedicated-host reboot matrix not run | service/container/gate restarts and fail-closed verified; `vm_acceptance.py` post-reboot phase ready | Eng | reboot matrix on dedicated host | Open |
| F-15 | Info | 29 pyright findings in tests/scripts (type-only) | classified | Eng | optional cleanup | Open |
| R-1 | Med | OKX continuous public connectivity unverified (block lifted once, observed again) | recorded incidents; re-verification loop | - | sustained observation | Open |
| R-2 | Med | Hosted CI never executed | local full regression | CEO | CI run | Open |
| R-11 | Med | Fee and trading-rule assumptions unverified | testnet `exchangeInfo` can be read at runtime | Eng | verified in an authorized M3-B | Open |
| OP-1 | Low | M2 observation depends on the laptop staying awake/online; coverage 62-84 % | supervisor, incidents recorded | - | n/a (waived gate) | Open |
| OP-2 | Info | Incident: a test invoked `bash` from PATH (the WSL launcher) and the shared `Ubuntu-24.04` distribution was found running afterwards | tests now locate Git Bash explicitly and refuse the WSL launcher; the distribution was not stopped by me; **it showed Stopped again at the start of the Mac work (stopped by its owner or other work)** | Eng | none | Disclosed, resolved
| F-19 | Med | Mac host (macOS) is the hypervisor and trust base: a compromise of its administrator rights defeats the VM boundaries; the Mac is not yet verified dedicated/hardened (Phase A not performed); FileVault vs unattended recovery trade-off open | host hardening list (sharing/Wi-Fi/Bluetooth off, firewall, no other use, no secrets on the host), unprivileged `_astavm`, root helper limited to a group socket, encrypted backups | CEO + reviewer | Phase A inventory reviewed; hardening applied and verified; reviewer accepts the residual; FileVault decision | Open |
| R-X | Med | Cross-architecture determinism of replay/checkpoint digests and ledger arithmetic (Windows x86 -> Linux ARM64) unverified; no ARM64 emulation available on the dev PC | restore-and-replay drill executed 2026-11-14 macOS arm64 -> aarch64 Linux (MAC-A10): all 1,800 stored checkpoint digests verified, identical output | Eng | remaining: the REAL M2 database from the Windows x86_64 PC (a different CPU architecture and OS than either side of this drill) | Narrowed (arm64<->aarch64 verified; x86_64 source not yet) |
| Closed | - | F-08 (obsolete dev-login file deleted), F-16 (evidence integrity), F-17 (gitleaks exceptions reviewed) | see R1/R2 | - | - | Closed |

## M3B-GOVERNANCE-01 additions (2026-11-14; detail in `docs/evidence/M3B-GOVERNANCE-01.md`)
| ID | Sev | Risk | Current mitigation | Owner | Closure condition | Status |
|---|---|---|---|---|---|---|
| G-01 | High | ExecutionGateway is not on the order path: `orders` never consults it (its only caller is the status probe) | no `ExchangePort` implementation exists, so nothing can send | Eng + reviewer | dispatch routed through the gateway/signed-privilege check at the port boundary, with a test that no dispatch bypasses it | **OPEN** (design gate) |
| G-02 | Med | Vault "synthetic only" is convention, not enforcement; no real backend | not wired to any runtime path; DEMO classes and READ/SPOT_TRADE only | Eng + reviewer | backend chosen, testnet-key guard, key ceremony (with F-05/F-06) | Open |
| G-03 | Med | Fill ledger silently drops a conflicting repeated trade; no DB-level overfill or state guard; rebuilt book lacks the order size | in-memory `FillBook` flags these; nothing consumes fills yet | Eng | content comparison, DB guards, tests | Open |
| G-04 | Med/Low | Intent channel: in-memory replay state (restart replays within 60 s), key id parsed before authentication, no audience binding, no authenticator | decision-id claim makes a replayed intent a no-op; channel is wired to nothing | Eng + reviewer | durable high-water mark, authenticated header, audience field, reviewed authenticator | Open |
| G-05 | Med | Reconciliation, reset classifier and kill switch not wired to risk halt, dispatcher day state or audit chain | pure logic, tested | Eng | wiring with end-to-end tests | Open |
| G-06 | Med | 8 mandatory security tests skipped on this Mac (no Docker daemon); earlier evidence omitted the skips | run on the previous host; MAC-A11 lab covers the rule sets | Eng | run on a Docker-capable host | Open (evidence corrected) |
| G-07 | Med | gitleaks full-history scan not executed (tool absent, Docker down, CI never run) | supplementary regex scan of all 99 commits: no key material | Eng/CEO | run in an approved environment, five reviewed fingerprints only | Open |
| G-08 | Med | Push path uses an overridden credential helper; inconsistent commit identities | none | CEO | repair keychain entry, set git identity, document the path | Open |
| G-09 | Info | Dashboard reachable on the tailnet (authenticated, credential-free) | Tailscale tailnet-only, session auth, secure cookie | CEO | review under F-01 | Accepted for now |
| F-12 | Med | (updated) dispatch/recovery, durable claim store, cancel/kill flush, fills ledger proven offline | see `ORDER-PATH-DESIGN.md` | Eng | real-exchange U-1..U-4 and connector test | Open (offline mitigations complete; unverified against a real exchange) |

## ASTA-DECISION-007 status update (2026-11-14; detail `docs/evidence/M3B-DECISION-007.md`)
G-01 **closed (offline scope)** - mandatory gateway boundary, import-graph and bypass tests. G-03 **closed** - DB-level fill guards, conflict log, atomic accounting. G-04 durable replay **closed**; pre-auth key-id parse and audience binding remain Low. G-05 **closed** within the order lifecycle; new **R-D1 (Med)**: `RISK_HALT` has no producer until a supervised bridge calls `SqlKillSwitch.trip`. G-07 **closed** (gitleaks 8.30.1 full history, no leaks). G-06 **open - 8 Docker/firewall gates BLOCKED**. G-02, G-08 open; G-09 accepted. **F-01, F-02, F-03 remain OPEN (High).**

## ASTA-M3B-R2 status update (2026-11-14; detail `docs/evidence/M3B-R2.md`)
G-02 **closed** (vault refuses non-synthetic values). G-04 Low items **closed** (no pre-auth JSON parse, per-receiver key allowlist). G-06 **closed**: all 8 Docker/firewall gates executed and passing on the Mac (Docker-VM boundary; scope limits stated). R-D1 logic **closed**; **R-D1b (Med) open**: the bridge process is not supervised/deployed. New note **N-1 (Low)**: market-data WebSockets fail TLS verification under the uv Python on this host unless `SSL_CERT_FILE` points at certifi's bundle (operator configuration). **F-01, F-02, F-03 remain OPEN (High).**

## ASTA-M3B-R3 additions (2026-11-14; detail `docs/evidence/M3B-R3.md`)
| ID | Sev | Risk | Owner | Status |
|---|---|---|---|---|
| H-1 | High-adjacent | the Mac is a shared workstation (unrelated Docker projects with all-interface ports, `exo`, Tailscale, cloud-sync updaters, same admin account); firewall off | CEO | OPEN (D-A); part of F-01/F-19 |
| H-2 | Med | 8 GiB VM design vs a contended 16 GiB host, 60 GiB free | Eng | mitigated by the 2-VM-now topology; measure in Phase C |
| P-5 | Med | Homebrew QEMU binary writable by the admin account | Eng | OPEN |
| P-7 | Med | `--firewall` breaks unrelated listeners | CEO | OPEN (D-A) |
| G-10 | Med | releaser identity is a parameter, not `session_user` | Eng | OPEN |
| G-11 | Low | latch-clear guard honours a custom setting (only roles with table UPDATE) | Eng | accepted |
| S-2 | Low | five stale `.gitleaksignore` fingerprints | CEO | OPEN |
| D-F | decision | FileVault vs unattended reboot | CEO | OPEN |
Closed in R3: P-1..P-4 (provisioning script), S-1 (scanner exception scope). R-D1b: units written, process not deployed, **now fail closed** (dead bridge closes the gate). **F-01, F-02, F-03 remain OPEN (High).**

## ASTA-M3B-R4 additions (2026-11-14; detail `docs/evidence/M3B-R4.md`)
| ID | Sev | Risk | Owner | Status |
|---|---|---|---|---|
| D-0 | Med, urgent | unrelated containers restarted by my R2 Docker start; `P3` (`0.0.0.0:3000`) and `P2-db` (`0.0.0.0:5442`) on all interfaces; P1 tunnel not running | CEO | OPEN |
| H-1 | High-adjacent | the Mac is a shared workstation (inventory, restorability verified for 2 DBs + 1 SQLite, plan and read-only check prepared; baseline check 10/14 FAIL) | CEO | OPEN |
| H-3 | Med | SSH (22), SMB (445), 88 reachable on the LAN; automatic login on; firewall off; FileVault off | CEO | OPEN |
| G-10 | Med | release identity was a parameter | Eng | **CLOSED** (0011: `session_user`) |
| P-5 | Med | QEMU binary/libraries writable by the admin | CEO/Eng | partly closed: pin + pre-boot refusal; regenerate at provisioning |
| S-2 | Low | five stale gitleaks fingerprints | CEO | analysed, kept until the pinned CI version confirms |
| F-07 | Med | alert-state changes now in the audit chain; shipper and off-host alerts not built | Eng | OPEN (designed) |
**F-01, F-02, F-03 remain OPEN (High).**

## ASTA-M3B-R5 additions (2026-11-14; detail `docs/evidence/M3B-R5.md`)
| ID | Sev | Risk | Owner | Status |
|---|---|---|---|---|
| D-0 | Med | two unrelated services published on all interfaces: **not reachable from the Internet** (3000, 5442, 445 refused from outside), reachable by the whole LAN and tailnet; auth: app login (sign-up off) / scram | CEO | OPEN: containment options C1-C5; C4 recommended now |
| D-0b | Med | **my R2 Docker start created five empty directories in the home folder** (birth 18:11:48) for a missing bind-mount source and restarted unrelated containers; my R4 inventory wrongly said a source tree was present (corrected) | CEO | OPEN (disclosed; nothing removed) |
| N-R1 | High (outside the Mac) | the router answers SSH on its WAN address from the Internet; Telnet/HTTP on the LAN | CEO | OPEN |
| N-R2 | Med | fw VM rules assume resolver/NTP at 192.168.1.1; this LAN uses `<router>` with DHCP DNS 8.8.8.8; NTP undefined | Eng | OPEN (set at deployment; allowlist NTP) |
| G-13 | Med | the dead-man's heartbeat attests the firewall VM only; app/bridge death is not carried off-host | Eng | OPEN (design) |
| G-12 | Med | this repository is public and its local history (not pushed) now contains project names, addresses and host-exposure details from R3/R4 | CEO | OPEN: review/redact before any push; R4/R5 docs now use aliases, history is unchanged |
| H-3 | Med | SSH/SMB/88 on the LAN, auto-login on, firewall off, FileVault off: runbook H1-H12 prepared | CEO | OPEN |
| CI | Med | hosted CI never run; workflow now has mandatory security jobs, a skip guard and a pinned gitleaks | CEO | OPEN (needs a push, not authorised) |
**F-01, F-02, F-03 and H-1 remain OPEN (High).**

| G-14 | Med | my commit `3b2a631` quoted a synthetic test string in a document: gitleaks (full history) reports 1 finding although it is not a secret; tip text fixed | CEO | OPEN: approve a sixth exact fingerprint or a rewrite of the unpushed commit |
