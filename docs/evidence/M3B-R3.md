# ASTA-M3B-R3: Mac mini security-deployment readiness (2026-11-14)

**Final status: BUILT — NOT ACCEPTED.** Credential readiness: **NO-GO.** Baseline `a6623a2`. Preparation and permitted non-credential validation only: no exchange credential (real or demo), no signing code, no authenticated order, no change to live-lock policy, no push, no acceptance tag, no `sudo`, no host change, no migration of legacy M2 sessions. Not an independent review; not signed as one. F-01, F-02, F-03 remain OPEN.

## Deliverables and where they are
| # | Deliverable | Location |
|---|---|---|
| 1 | Mac readiness audit | `docs/m3b/MAC-READINESS-AUDIT.md` |
| 2 | Final security topology | `docs/m3b/SECURITY-TOPOLOGY.md` |
| 3 | Provisioning-script impact report | `MAC-READINESS-AUDIT.md` s.5-6 (+ fixes P-1..P-4 in the script, tested) |
| 4 | Vault backend recommendation | `docs/m3b/VAULT-AND-BACKUP.md` s.1-3 (OpenBao transit/Ed25519; fallback age + systemd-creds) |
| 5 | Backup-key ceremony | `VAULT-AND-BACKUP.md` s.4 (replace the key pair, do not relocate it) + executed synthetic rehearsal s.5 |
| 6 | Bridge supervision design | `SECURITY-TOPOLOGY.md` s.3-4 + units `asta-risk-bridge.service`, `asta-security-monitor.{service,timer}`, `asta-security-alert@.service` |
| 7 | Independent review checklist | `docs/m3b/INDEPENDENT-REVIEW-CHECKLIST.md` |
| 8 | Remaining findings and owners | s.4 below |
| 9 | M3-B readiness GO/NO-GO | s.5 below |

## 1. What was built or changed (all offline, all tested)
* **Provisioning script hardened (P-1..P-4), not run:** no usable password on `_astavm`; refuses protected `--vm-base` locations and any non-empty directory not already owned by `_astavm` (it would otherwise `chown -R` it); creates the symlink parent; warns that the Homebrew QEMU is not root-owned. +3 tests (11 total for the script).
* **Rollback script** `rollback-host.sh` (dry-run default, safe default keeps data/account/firewall, destructive flags opt-in, never touches other projects): 4 tests; dry run executed here, changed nothing.
* **Fail-closed bridge supervision:** migration `0010`: `risk_halt_bridge_heartbeat` + `bridge_heartbeat()`; the bridge beats on each successful poll; `BridgedKillState` (latched OR heartbeat missing/stale OR database unreadable = halted) is the kill state the order path must be built with; `orders/monitor.py` (exit 0/1/2) with systemd timer and `OnFailure` alert; hardened systemd units (tested statically like the other units). 8 PostgreSQL tests (dead bridge closes the gate and a restarted one reopens it; dispatch refused while the bridge is dead; unreadable database closes the gate; monitor outcomes).
* **Least-privilege database roles** `pg_roles_m3b.sql` (`asta_bridge`, `asta_orders`, `asta_monitor`, `asta_release`) with peer-only `pg_hba`/`pg_ident` entries; migration `0010` also makes the halt functions `SECURITY DEFINER` with a pinned `search_path` and revokes `EXECUTE` from PUBLIC. Tested on PostgreSQL: the bridge can latch and beat but cannot release, edit halt tables, add releasers or touch orders/fills; the orders role cannot release; the release role can only release and only as a listed actor; PUBLIC has no `EXECUTE`.
* **Scanner fix S-1:** the allow-list exception now applies to the matched text, not the whole line (test pins it).
* **Synthetic backup-key rehearsal** executed: 8/8 checks, real identity untouched (metadata compared; the key file is never opened).

## 2. Verification run (offline; Docker boundary containers up; not deployed-host evidence)
| Check | Result |
|---|---|
| Python suite (PostgreSQL 16, UTC) | **1,317 passed, 5 skipped, 0 failed** (all 8 Docker/firewall gates ran and passed; the 5 skips are the classified non-security ones: 4 opt-in network tests, 1 obsolete PowerShell parse) |
| ruff check / format, mypy (122 files) | clean |
| pip-audit; `pnpm audit --prod`; `tsc --noEmit`; web unit tests | no known vulnerabilities; no known vulnerabilities; clean; 48/48 |
| gitleaks full history (8.30.1) and `scripts/secret_scan.py` | see the final commit check below: no leaks / 0 findings |
| M0-M2 frozen evidence; paper ledger | frozen evidence intact; paper sessions replay 900/900 checkpoints, 0 discrepancies, fills digests unchanged |
| Live-lock paths (`tests/live_lock`, `risk`, `egress`, `environment`, `exchanges`) vs `fb7770b` | unchanged |
| Migrations | head `0010` applied to the dev database (additive; no paper table touched; no session migrated) |
| Dry runs on the real Mac | `provision-host.sh` (with and without `--vm-base`), `rollback-host.sh`, `mac-inventory.sh`: no change made |
Design validation, the Docker gates and the offline tests do **not** prove deployed host isolation: the boundary does not exist on this Mac.

## 3. New findings this round
| ID | Sev | Finding | Status |
|---|---|---|---|
| H-1 | **High-adjacent (host)** | the Mac is a shared workstation: unrelated Docker projects (two publish on all interfaces), `exo` listening on `*`, Tailscale, cloud-sync updaters, browsers, same admin account; firewall off, Wi-Fi on | OPEN, owner decision D-A; part of F-01/F-19 |
| H-2 | Med | capacity: design 8 GiB RAM vs a contended 16 GiB host; 60 GiB free | mitigated by the 2-VM-now topology (estimates, measure in Phase C) |
| P-1..P-4 | Med/Low | provisioning script: no password attribute; unrestricted `chown -R`; symlink parent; QEMU ownership | **fixed** |
| P-5 | Med | QEMU binary from Homebrew is writable by the admin account | OPEN: hash pin + pre-boot check, or root-owned copy (provisioning decision) |
| P-6 | Low | `tar -xzf -C /` of the socket_vmnet archive: inspect members first | runbook |
| P-7 | Med | `--firewall` would break unrelated listeners (exo, Docker-published ports) | depends on D-A |
| G-10 | Med | releaser identity is a parameter, not `session_user` | OPEN (next authorised change) |
| G-11 | Low | latch-clear guard honours a custom setting; only roles with table `UPDATE` can use it (none of the service roles) | accepted, documented |
| S-1 | Med | scanner exception skipped whole lines | **fixed** |
| S-2 | Low | five stale `.gitleaksignore` fingerprints | OPEN (owner) |
| R-D1b | Med | bridge process not yet deployed/supervised (units written, not installed) | OPEN; **now fail closed** (a dead bridge closes the gate) |
| D-F | decision | FileVault: unattended reboot vs data-at-rest | OPEN (owner) |

## 4. Remaining findings and owners
| ID | Sev | Owner | Next step |
|---|---|---|---|
| F-01 | High | CEO + Eng | deploy credential VM only after F-02/F-03 and D-A; C1/C6 |
| F-02 | High | CEO | review and run `provision-host.sh --apply` (separate authorisation, exact command listed in the audit s.5-6); then C1-C10, H1-H8, R1-R10 |
| F-03 | High | CEO | designate the reviewer; hand over the bundle and `INDEPENDENT-REVIEW-CHECKLIST.md` |
| H-1 / D-A / D-F | host | CEO | dedicate the Mac or accept the residual; FileVault decision |
| F-05/F-06 | Med | CEO + reviewer | approve OpenBao-transit (or the fallback); run the key ceremony; extend signed configs with expiry/replay |
| F-07 | Med | Eng | ship proxy/vault/bridge logs to the audit chain; off-box alert channel |
| G-10, P-5 | Med | Eng | `session_user`-based release; QEMU integrity pin |
| G-08, S-2, R-2 | Med/Low | CEO | repair push identity; prune stale gitleaks fingerprints; run hosted CI (requires a push, not authorised) |
| R-D1b | Med | Eng | install units at provisioning; test R6/R8 |

## 5. M3-B readiness: **NO-GO**
All preparation items are complete and tested offline. The host is not provisioned, is not dedicated, and no independent party has verified anything; the credential VM, vault backend, key ceremony, hosted CI, off-box alerting and a fresh CEO authorisation are all outstanding. **BUILT — NOT ACCEPTED.**
