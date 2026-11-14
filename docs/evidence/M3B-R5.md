# ASTA-M3B-R5: host exposure containment and security deployment preparation (2026-11-14)

**Final status: BUILT — NOT ACCEPTED.** Credential readiness: **NO-GO.** Baseline `ddf7d77`. Read-only audits, offline engineering, non-destructive tests and preparation only. **Not done (by restriction):** no unrelated service was stopped, restarted, recreated or removed; nothing was pruned or deleted in Docker (see s.6 for my own scratch objects); no workload migration; no privileged command, no `sudo`, no host firewall change; no credential, signing code or external order path; ExecutionGateway default-deny and the live-lock unchanged; no push, no tag, no acceptance, no migration of M2 sessions. Independent approval **unsigned**. Names of unrelated projects and network addresses are kept **out of the repository** (aliases P1..P8; private map in `~/asta-private/`).

## Required completion report
| # | Item | Where |
|---|---|---|
| 1 | D-0 network exposure assessment | `docs/m3b/D0-EXPOSURE-AND-MIGRATION-REGISTER.md` s.1 |
| 2 | Per-service containment recommendations | same, s.3 (C1-C5; C4 now, C2 least invasive, C3 permanent) |
| 3 | Cloudflare tunnel impact | same, s.2 |
| 4 | Docker workload migration register | same, s.4 |
| 5 | Backup and rollback verification | same, s.4 (7 volumes restored, 10 images archived) and `scripts/docker_pg_volume_verify.py` |
| 6 | Host-hardening execution plan | `docs/m3b/HOST-HARDENING-RUNBOOK.md` (H1-H12) |
| 7 | Privileged provisioning impact matrix | same, s.2 |
| 8 | Actual free storage and VM capacity plan | `docs/m3b/VM-READINESS-REPORT.md` s.1, `CAPACITY-ASSESSMENT.md` |
| 9 | Independent-review evidence requirements | `VM-READINESS-REPORT.md` s.7, `INDEPENDENT-REVIEW-CHECKLIST.md` |
| 10 | Regression and scan results | s.2 below |
| 11 | Updated F-01, F-02, F-03, H-1, H-3 | s.3 below |
| 12 | Final local commit | in the summary message (the last commit of this phase) |
| 13 | GO/NO-GO | s.4 below |

## 1. Key findings of this phase
* **D-0 is not an Internet exposure.** From an outside vantage the router's WAN address refuses 3000, 5442 and 445. P3 (login, sign-up disabled) and P2-db (scram) are open to every LAN and tailnet device because the macOS firewall is off. Router rules beyond those ports could not be read.
* **N-R1 (outside the Mac):** the router itself answers **SSH on its WAN address** (banner RouterOS) and Telnet/HTTP on the LAN. Largest externally reachable surface found. Not tested beyond the banner.
* **I made a mess and my R4 inventory was wrong.** When I started Docker Desktop in R2, Docker created five **empty directories** in the home folder for a missing bind-mount source (birth time = Docker's start second) and restarted unrelated containers. P1's source tree is **not on this Mac** (R4 said it was); no compose project has its source here. The P1 tunnel was already down since ~11-08 (Docker was off) and cannot start without its config file.
* **Restorability:** all 7 previously unverified PostgreSQL volumes restore with identical table/row counts (copy-of-volume + throw-away servers, sources read-only); P1 and P2 databases and P3's SQLite verified in R4. Ten locally built images (no registry copy) are archived on the external drive, 0.74 GiB, checksum verified.
* **Storage:** the internal container has 59.3 GB unallocated; VM provisioning must wait until Docker's 50.6 GiB leaves or a separate APFS volume exists.
* **VM readiness:** bypass checks 38/38 (scratch kernel), firewall persistence identical across a fresh load, a service restart and a real **guest** reboot, synthetic vault recovery 11/11, QEMU pin re-verified; **host reboot/power-loss tests prepared, not run**.
* **Network-path dependencies:** the firewall VM's rules assume resolver/NTP at `192.168.1.1` (N-R2); this LAN's DHCP hands out 8.8.8.8 and no NTP is defined: **must be fixed at deployment**.
* **Hosted CI** now has mandatory security jobs (backend, security, egress-boundary with the Docker gates, web), a skip guard that fails the build if any security gate is skipped, least-privilege permissions, `persist-credentials: false`, a pinned gitleaks and an aggregate `required` job. It has **never run** (needs a push).
* **Dead-man's switch** implemented (disabled until configured); **gap G-13**: it attests the firewall VM only.
* **gitleaks exceptions:** reviewed, kept, justification written (`GITLEAKS-EXCEPTIONS-REVIEW.md`).

## 2. Verification (offline; Docker boundary containers up; not deployed-host evidence)
See "Results" at the end of this file.

## 3. Status of the open findings
| ID | Status | What changed in R5 |
|---|---|---|
| **F-01** credential-bearing processes outside an isolated runtime | **OPEN (High)** | no credential VM; dev stack still under the admin account; H1/H2 plan |
| **F-02** boundary not independently enforced on dedicated infrastructure | **OPEN (High)** | scratch-kernel evidence extended (38/38 + persistence); provisioning blocked by storage and the shared host; `sudo` window needed |
| **F-03** no independent security review | **OPEN (High)** | evidence requirements extended; **unsigned** |
| **H-1** the Mac is a shared workstation | **OPEN** | inventory corrected, containment/migration plans, all DBs restore-verified, images archived; nothing migrated; `host-separation-check.sh` baseline still FAIL |
| **H-3** LAN-exposed services, auto-login, FileVault/firewall off | **OPEN** | runbook H1-H12 with commands, effects, rollback; nothing executed |
| D-0, D-0b, N-R1, N-R2, G-12, G-13, CI | OPEN | see `RESIDUAL-RISK-REGISTER.md` |
| G-10, G-04, bridge fail-closed | preserved | tests green; `session_user` release identity unchanged |

## 4. M3-B readiness: **NO-GO**
Preparation is complete and tested offline. Unchanged blockers: the host is not dedicated, not hardened, not provisioned; unrelated workloads and exposures remain; the router's management plane is exposed; no independent reviewer; no vault backend or key ceremony; no off-host alert path; CI never run; no authorisation. **BUILT — NOT ACCEPTED.**

## 6. My own writes in this phase
Seven anonymous **scratch Docker volumes** from my restore-target containers remain (a deletion attempt by exact name was refused by the environment's safety check; names in the private map; the script now cleans up after itself); a backup folder `asta-r5-docker-preservation/` on the external drive (delete if unwanted); `~/asta-private/r5/` (mode 0700). Everything else was read-only.

## Results (final run, offline; Docker boundary containers up; not deployed-host evidence)
| Check | Result |
|---|---|
| Python suite (PostgreSQL 16, UTC) | **1,341 passed, 5 skipped, 0 failed** (all 8 Docker/firewall gates ran; the 5 skips are the reviewed non-security ones, confirmed by the new `scripts/ci_skip_guard.py` run on the real log: "only reviewed skips") |
| ruff check/format; mypy (122 files) | clean |
| pip-audit; `pnpm audit --prod`; `tsc`; web unit tests (48) | no known vulnerabilities; no known vulnerabilities; clean; pass |
| gitleaks 8.30.1 full history; `scripts/secret_scan.py` | see the commit check (no leaks / 0 findings expected and re-run after the commits) |
| M0-M2 frozen evidence; paper ledger | intact; 900/900 checkpoints, 0 discrepancies, fills digests unchanged |
| Live-lock paths vs `fb7770b`; ExecutionGateway | unchanged; default-deny |
| QEMU pin | verified (31 files) |
| Executed on scratch VMs (user level) | firewall persistence across guest reboots (hash `9eda96b8857e0fe6` stable); synthetic vault recovery 11/11 |
| Executed read-only against the real host/Docker | exposure probes (LAN, tailnet, outside vantage), logs/auth summaries (counts only), 7-volume logical restore, image archive |
| Not executed (needs the approved window or a push) | H1-H12, provisioning, host reboot/power-loss, `host-separation-check.sh` target state, off-host alert drills, vault backend rehearsals, hosted CI |
