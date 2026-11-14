# Independent review checklist, baseline `a6623a2` + R3 + R4 (ASTA-M3B-R3/R4)

**This is a preparation document, not an independent review, and it is not signed as one.** F-01, F-02, F-03 are OPEN. Entry points: `docs/m3a/INDEPENDENT-REVIEW-PACKAGE.md` (updated), `docs/m3a/REVIEW-INDEX.md`, `docs/evidence/M3B-R3.md`.

## 1. Baseline and how to obtain it
Repository `https://github.com/kimsudara-kd/asta`, branch `main`. **Remote head is `924da22`; the reviewed baseline `a6623a2` and the R3 commits exist only in the local repository** (nothing was pushed, G-08): the reviewer needs a local bundle (`git bundle create asta.bundle main`) delivered by the owner, with its SHA-256 sent out of band. Tags `m0/m1/m2-accepted-with-residual` are local; **no M3 tag exists**.

## 2. All High findings (none closed)
| ID | Finding | Owner | State | Closure evidence the reviewer must see |
|---|---|---|---|---|
| F-01 | credential-bearing processes could run outside an isolated runtime; today the dev stack and unrelated software share the admin account | CEO + Eng | OPEN | credential VM deployed; C1/C6 executed (no route from legacy/app side); reviewer accepts the residual of the shared host (H-1) |
| F-02 | no independently enforced boundary on dedicated infrastructure; the Docker gate shares Docker Desktop's kernel (F-18) | CEO (sudo/hardware) + Eng | OPEN | `provision-host.sh --apply` run by the owner after review; C1-C10 + R1-R10 + H1-H8 executed incl. a real reboot; reviewer verifies |
| F-03 | no independent security review | CEO | OPEN | signed report; Critical/High closed or formally accepted |
Related, not High: G-10 (releaser identity is a parameter), H-1 (host not dedicated), F-05/F-06 (vault backend, key ceremony), F-07 (log shipping/alerting), G-08 (push identity), R-D1b (bridge process not yet deployed), R-2 (hosted CI never executed), R-11, R-1, R-X.

## 3. The five skipped tests at the R2 baseline (classification)
| Test | Why skipped | Class | Evidence it is covered |
|---|---|---|---|
| `contracts/test_live_public.py` (3 tests) | needs `ASTA_RUN_NETWORK_TESTS=1` and network | **not a security gate** (public-data contract) | run with the flag in R2: 4 passed (needs `SSL_CERT_FILE=certifi` on this host) |
| `integration/test_e2e_live.py` | same | not a security gate | same |
| `m3a/test_provisioning_artifacts.py` (PowerShell parse of the Hyper-V script) | no PowerShell on macOS | **obsolete** (Windows/Hyper-V option withdrawn by MAC-01) | n/a |
None of the five is a mandatory security gate. (With the Docker boundary containers stopped, two further `test_egress.py` tests skip by design ("boundary not running"); they are mandatory gates and were run and passed with the containers up: `docs/evidence/M3B-R2.md` s.4, re-run in R3.)

## 4. Review of the historical scanner exception
Facts: `scripts/secret_scan.py` allow-lists the fragment `Bkq3ZtYw9mNpXr2L`, the start of a **fabricated 64-character key-shaped test vector** that I wrote into `tests/orders/test_channel_hardening.py` (commit `4a39b4a`) to prove the vault refuses key-shaped values; it is not derived from any credential, appears in no other file (`git log -S`), and the test now builds an equivalent at runtime. History was not rewritten, so the scanner needs the exception. gitleaks (authoritative, `.gitleaks.toml`) never flagged it: 101 commits, no leaks, also with the ignore file emptied.
Findings: **S-1 (fixed in R3):** the exception mechanism skipped the *whole line* if it contained any allow token (`test-only`, `change-me`, ...): a real key on a line mentioning "test-only" in a comment would have passed. It now applies to the *matched text only* (`tests/m3a/test_credential_hygiene.py` pins both behaviours). **S-2 (open, Low):** the five fingerprints in `.gitleaksignore` did not fire in gitleaks 8.30.1 and look stale; recommend the owner removes them (a guard test pins the list) so that no unnecessary suppression remains. **S-3 (info):** the bespoke scanner is a convenience; gitleaks in CI plus the local run is the control, and hosted CI has never executed (R-2).

## 5. Acceptance tests to run on the provisioned Mac (none executed: the Mac is not provisioned)
Host-boundary tests (macOS side; evidence = command output + JSON, re-run by the reviewer):
| # | Test | Pass criterion |
|---|---|---|
| H1 | service account | `dscl . -read /Users/_astavm` shows hidden, `/usr/bin/false`, `/var/empty`, no usable password; `id _astavm` has no admin/wheel membership |
| H2 | VM data isolation | as another user `ls /var/lib/asta-vm` and `cat` a disk: permission denied; mode 0700 `_astavm`; volume is APFS with ownership enforced |
| H3 | launcher integrity | `/usr/local/libexec/asta/qemu-vms.sh` root:wheel 0755 and not writable by `_astavm`; plists root:wheel 0644 and `plutil -lint` OK |
| H4 | QEMU integrity | SHA-256 of the QEMU binary and firmware equals the value recorded at provisioning and is re-checked before each boot |
| H5 | uplink | only `socket_vmnet` (root) has `en0` bridged; its socket is group `_astavm` 0660 in `/var/run`; no ASTA process listens on a host interface (`lsof -iTCP -sTCP:LISTEN`) |
| H6 | no host presence on guest links | `ifconfig` shows no interface or address for the fw<->app / fw<->cred links; sockets live only in the 0700 run directory |
| H7 | egress capture | `tcpdump` on `en0` during the 78-check suite shows only the firewall VM's allowlisted 443/9443 flows |
| H8 | fail closed | stop `com.asta.vm.fw`: zero connectivity from `asta-app`; start it: restored with no manual step |
Guest tests: **C1-C10** of `VM-BOUNDARY-DESIGN.md` R3-2 (run `infra/security-host/acceptance/vm_acceptance.py` and `vault_validation.py` per VM) plus the order-path controls below.
Reboot acceptance (run `vm_acceptance.py ... pre`, reboot, `... post --pre`): R1 guest reboot; R2 **host reboot** (power cycle); R3 daemons loaded without a login; R4 VMs back within the agreed time; R5 firewall ruleset hash and routes identical; R6 `asta-risk-bridge` active and heartbeat < 30 s; R7 monitor exit 0 or warning only; R8 kill-switch state preserved (a latched halt is still latched; an unlatched one still open); R9 PostgreSQL recovered, migrations at head, audit chain verifies; R10 paper supervisor reconciled and the gap recorded. Plus snapshot revert and power loss from C7.
Order-path controls (offline, already in the suite; the reviewer re-runs `pytest tests/orders tests/integration/test_m3b_integrity.py tests/integration/test_risk_bridge.py tests/integration/test_bridge_supervision.py tests/live_lock`): gateway bypass tests, fill uniqueness/overfill/concurrency/crash, durable replay, latched kill switch, bridge heartbeat fail-closed, role least-privilege matrix.

## 6. Exact reviewer evidence required
1. A local bundle of the baseline with SHA-256 verified out of band; `python scripts/review_manifest.py --verify` and `scripts/verify_frozen_evidence.py` clean.
2. Written answers to the questions of `INDEPENDENT-REVIEW-PACKAGE.md` s.4 plus: is the 2-VM-now / cred-VM-later topology acceptable; is the shared-host residual (H-1) acceptable or must the Mac be dedicated; FileVault position (D-F); OpenBao-transit vs the fallback (`VAULT-AND-BACKUP.md`); G-10 closure design.
3. The reviewer's **own** execution transcripts of: the 8 Docker/firewall gates, H1-H8, C1-C10, R1-R10, the key-ceremony rehearsal, an unseal/rotation/revocation drill (when a real backend exists), and a full-history gitleaks run.
4. A review of: `provision-host.sh` + `rollback-host.sh` (diff against the audited versions), the launchd plists, `qemu-vms.sh`, the three nftables files, the proxy, the migrations 0006-0010 and the role SQL, `core/order_gate.py`, `orders/boundary.py`, `orders/channel.py`, `orders/risk_bridge.py`, `security/vault.py`.
5. A written opinion: credential readiness "ready" / "ready with conditions" / "not ready", every Critical/High closed or formally accepted by the CEO, signed and dated by the reviewer. **Sign-off block: to be completed only by the designated independent reviewer.**


## 7. R4 additions: baseline, unresolved findings, and the network + host-hardening acceptance matrix
**Baseline for the reviewer:** local commits after `c10da4a` carry the R4 work (remote head still `924da22`). Still **unresolved: H-1 (the Mac is a shared workstation), F-01, F-02, F-03.** Newly relevant facts: the unrelated Docker projects were restarted by Docker Desktop when I started it for the gates (`HOST-DEDICATION-INVENTORY.md` s.0, decision D-0); LAN-reachable SSH (22), SMB (445) and 88 with the application firewall off; automatic login on; FileVault off.
Closed in R4 (verify, do not trust): G-10 (release identity = `session_user`, migration 0011, tests), S-2 analysis (stale gitleaks fingerprints: kept, CI version pinned), P-5 partly (QEMU pin + pre-boot refusal; owner regenerates the pin at provisioning).
**Matrix (each row: control, how the reviewer verifies, status today).** Status: NOT RUN = needs the provisioned host; DONE-SCRATCH = executed in a scratch VM/namespace (design evidence only); DONE = executed on this repository.
| # | Control | Verification | Status |
|---|---|---|---|
| N1 | app VM has exactly one NIC (unix-socket link to fw), no host-network device | `qemu-vms.sh run app` command line; `ip -o link` in the VM | DONE (static), NOT RUN (deployed) |
| N2 | no global IPv6 on app; IPv6 to public resolvers and link-local blocked | `ip -6 addr`; netns lab + real VM probes | DONE-SCRATCH |
| N3 | direct-IP egress blocked (default route via fw, forwarding on) | lab + 78-check suite through the proxy | DONE-SCRATCH + Docker gate |
| N4 | DNS (UDP/TCP 53, DoT 853) blocked from app; only fw may resolve, as root | lab | DONE-SCRATCH |
| N5 | metadata, RFC1918, CGNAT, QUIC blocked | lab | DONE-SCRATCH |
| N6 | alternate routes (on-link /32, lower-metric default, policy rule) open nothing | lab | DONE-SCRATCH |
| N7 | compromised app (own firewall flushed) still blocked by fw; fw down = no connectivity | lab + C8 | DONE-SCRATCH |
| N8 | uplink only through root `socket_vmnet` on the fw VM; capture on `en0` shows only allowlisted flows | H5, H7 | NOT RUN |
| N9 | credential VM cannot start and the fw VM has no cred link in the two-VM phase | `qemu-vms.sh run cred` / fw dry run | DONE (tests) |
| N10 | host-level: H1-H8, B1-B10 of `HOST-SECURITY-CHECKLIST.md`, C1-C10, R1-R10 | transcripts | NOT RUN |
| S1 | `host-separation-check.sh` exits 0 | run after migration | baseline FAIL (10/14) |
| S2 | FileVault on with a tested recovery key; firewall on; no auto-login; remote access as approved | B1, B4, B8 | NOT RUN |
| S3 | QEMU pin verifies; a modified library blocks VM start | `qemu_pin.py verify`, B6 | DONE (pin), NOT RUN (B6 on host) |
| S4 | order-path guards: gateway, durable replay, fills, latched kill switch, heartbeat, role matrix, `session_user` release | `pytest` set in s.5 | DONE |
| S5 | alert path survives host loss (dead-man's switch) | kill-each-component drill | NOT RUN (not built) |
**Leave unsigned:** independent approval requires a qualified reviewer to execute the NOT RUN rows and write the opinion; nothing in this repository is signed on their behalf.
