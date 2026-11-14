# Mac mini security-host readiness audit (ASTA-M3B-R3, workstream A)

Baseline `a6623a2` (+ the R3 commits). **Read-only inspection plus offline preparation. `sudo` was not used and cannot be (a password is required); nothing on the host was changed by the audit.** This is a readiness audit of design and artifacts, **not** evidence that the deployed host is isolated: the boundary does not exist on this Mac yet. Serial number, hardware UUID and other identifiers were deliberately left out of this document.

## 1. Actual host (2026-11-14, `mac-inventory.sh`, `sysctl`, `diskutil`, `lsof`, `pmset`)
| Item | Observed | Consequence |
|---|---|---|
| Model / chip | Mac mini `Mac16,10`, Apple M4, 10 cores (4 P + 6 E) | HVF virtualization supported (`kern.hv_support = 1`) |
| macOS | 26.5.2 (25F84), Darwin 25.5.0 arm64; SIP enabled | matches the design target |
| RAM | 16 GiB (`hw.memsize`); ~48 % reported free at the time; 743,346 cumulative page-outs since boot | memory is already contended (see s.4) |
| Storage | internal APFS container 245 GB; `/System/Volumes/Data` 143 GiB used, **60 GiB free (71 % used)**; FileVault **Off** | the design's 78 GB thin VM disks + 20 GB backup do not fit comfortably; external APFS (ownership enforced) needed |
| Network | Ethernet `en0` <LAN-IP>/24 (the LAN), Wi-Fi on, three further Ethernet adapters present but down | bridged uplink via `en0` is possible; Wi-Fi is not disabled |
| Firewall | macOS application firewall **disabled** | no inbound filtering on the host today |
| Power | `sleep 0` (prevented), `autorestart 1` | restart after power loss is enabled |
| Admin | single human account `apple` (admin); `sudo` needs a password | privileged provisioning is impossible without the owner |
| Tooling present (Homebrew, owned by `apple`) | QEMU 11.1.3, age 1.3.2, gitleaks 8.30.1; Docker Desktop 29.6.2 (aarch64 VM, 7.75 GiB) | `socket_vmnet` is **not** installed (needs the pinned tarball) |
| Dev stack | per-user launch agent `com.asta.dev-stack`: API 127.0.0.1:4640, dashboard 127.0.0.1:3640, PostgreSQL 127.0.0.1:5640; Tailscale Serve (tailnet-only) on :8443 | credential-free, loopback + tailnet |

## 2. ARM64 compatibility (validated)
Native `arm64` Python 3.12 (universal binary); the 79 locked Python packages: 67 pure Python, 12 native with arm64 wheels, only the dev-only `pgserver` lacks a Linux aarch64 wheel (`scripts/arm64_compat_audit.py`, re-run today, unchanged); 36 manylinux aarch64 runtime wheels (`docs/evidence/m3a/mac-arm64-runtime-wheels.sha256`); the **full suite runs natively on macOS arm64: 1,295 passed**; Docker gates run on the aarch64 Docker VM; the earlier cross-architecture restore-and-replay drill (MAC-A10) and the 22/22 namespace firewall lab on a real aarch64 kernel (MAC-A11). **Not validated:** the final runtime inside the three Ubuntu aarch64 VMs under QEMU+HVF (Phase C), x86_64-origin data (R-X).

## 3. Isolation from unrelated projects: **NOT met (finding H-1)**
The design assumes a dedicated Mac ("no other use"). The inspected host is a shared workstation:
* Docker Desktop runs four unrelated projects' containers (`P2-db`, `P1-{web,api,db}`, `P3`); two publish on **all interfaces** (`*:3000`, `*:5442`).
* `exo` (distributed inference) listens on `*:49366` and `*:52415`; a `homebrew.mxcl.tailscale` daemon, `io.exo.networksetup`, OneDrive/Office updaters, Chrome/VS Code/Claude Code sessions run under the same admin account.
* The macOS firewall is off; Wi-Fi is on; the same account `apple` is admin and owns the Homebrew tree that supplies QEMU.
Effect: the host administrator account is the trust base of every VM boundary (F-19); on this machine that account also runs unrelated, network-listening software. **Owner decision D-A:** (1) dedicate the Mac to ASTA (move the other workloads off, disable Wi-Fi/sharing), or (2) accept and record the weaker isolation, or (3) use a second small machine for the credential VM when M3-B is authorised. Until chosen, F-19 stays open and no isolation claim is made.

## 4. Capacity versus the three-VM design (16 GiB / 60 GiB free)
Design allocation 8 GiB RAM (fw 1 + app 5 + cred 2) and 78 GB thin disks. Today: Docker's VM is 7.75 GiB, one Virtualization.framework process holds ~1.1 GiB, and macOS already paged out heavily. Adding 8 GiB would very likely cause sustained swapping and unpredictable latency for the supervised services. Recommendation in `SECURITY-TOPOLOGY.md`: **two VMs now (~4 GiB), the credential VM only when M3-B is authorised (+1 GiB)**, disks on an external APFS volume with ownership enforced, and a cap on Docker's memory or removal of the unrelated containers. (Figures are estimates, not measurements, until Phase C.)

## 5. `provision-host.sh`: line-by-line review (as of this commit)
Reviewed in full (every line read; `sh -n` clean; the dry run executed with and without `--vm-base`). Effects by section:
| Section (script) | What it does | Changes on the Mac | Review result |
|---|---|---|---|
| header, option parsing | `--apply`, `--load`, `--firewall`, `--vm-base`, `--socket-vmnet-*`; dry run by default; `set -eu` | none | OK; unknown options exit 2 |
| preflight (always read-only) | Apple-silicon check, `kern.hv_support`, root only for `--apply`, files present, `plutil -lint` of 4 plists, QEMU presence, free space, `--vm-base` volume type (APFS/HFS+, ownership, not `noowners`), tarball SHA-256 | none | OK. **P-2 fixed:** `--vm-base` could be any directory and is later `chown -R`: now refuses protected locations (`/`, `/Users/<home>`, `/Library`, `/etc`, `/opt`, `/private`, `/Applications`, ...; only `/Users/Shared/*` and new directories elsewhere) and any existing non-empty directory not already owned by `_astavm`. **P-4 noted:** warns that the Homebrew QEMU is not root-owned |
| 1 service account | `dscl` creates hidden user/group `_astavm` (id 330-399, `/usr/bin/false`, `/var/empty`) | **adds a user and a group** | **P-1 fixed:** now also sets `Password '*'` (no usable password hash) |
| 2 directories | `/var/lib/asta-vm` (+`run images vars disks seeds`) `_astavm` 0700, `/var/log/asta` `root:_astavm` 0770, `/usr/local/libexec/asta` `root:wheel` 0755, root-owned `qemu-vms.sh` 0755; optional symlink to `--vm-base` | **creates directories, a launcher, an optional symlink** | OK. **P-3 fixed:** the parent of the symlink (`/var/lib`) is created before `ln -s` |
| 3 socket_vmnet | only from a tarball whose SHA-256 you supply; extracted to `/opt/socket_vmnet`, root-owned, `go-w` | **adds a root-run network helper** | OK. **P-6 (runbook):** list the archive (`tar -tvzf`) before extracting; members are limited to `opt/socket_vmnet` but symlink members must be checked by a human |
| 4 LaunchDaemons | installs four plists root:wheel 0644; loads only with `--load` and only if socket_vmnet and the VM disks exist | **adds four system daemons** (`socket_vmnet` runs as root, bridged on `en0`, socket group `_astavm`; three VM daemons run as `_astavm`) | OK; not loaded by default |
| 5 firewall | only with `--firewall`: application firewall on + stealth | **changes a host-wide setting** | OK as a flag. **P-7:** it will prompt/deny inbound for the unrelated `exo` listener and for Docker-published ports (`*:3000`, `*:5442`): decide D-A first |
| closing notes | nothing for FileVault, Wi-Fi/Bluetooth/sharing, power | none | deliberate |
Not covered by the script and required separately: Homebrew QEMU integrity (**P-5**: ASTA runs `/opt/homebrew/bin/qemu-system-aarch64` as `_astavm`; the binary is writable by the admin account; record its SHA-256 at provisioning and re-check before every VM boot, or copy it into a root-owned tree), Ubuntu image and cloud-init seeds (Phase C, `qemu-vms.sh prepare`).

## 6. Impact report (what `--apply [--load] [--firewall]` would change)
| Class | Items |
|---|---|
| Users / groups | `_astavm` (user + group, uid/gid 330-399, hidden, no shell, no home, no password) |
| Files / directories | `/var/lib/asta-vm` (0700 `_astavm`; or `--vm-base` + symlink), `/var/log/asta` (0770 `root:_astavm`), `/usr/local/libexec/asta/qemu-vms.sh` (root:wheel 0755), `/opt/socket_vmnet/**` (root:wheel, only with the tarball), `/Library/LaunchDaemons/com.asta.{socket_vmnet,vm.fw,vm.app,vm.cred}.plist` (root:wheel 0644) |
| Services | four LaunchDaemons (loaded only with `--load`): root `socket_vmnet` (bridged `en0`), three QEMU VM daemons as `_astavm` (`KeepAlive`, throttle 10 s) |
| Permissions | VM disks/sockets reachable only by `_astavm`; launcher not writable by `_astavm` |
| Network | a bridged L2 uplink on `en0` for the firewall VM only (new MAC/IP on the LAN); app/cred links are unix sockets that exist on no host interface |
| Firewall | unchanged unless `--firewall` |
| Not changed | FileVault, Wi-Fi/Bluetooth/sharing, power, Docker, Homebrew, other projects, the dev stack |

## 7. Rollback and recovery (prepared, not run)
`infra/security-host/mac/rollback-host.sh` (dry run by default, root for `--apply`, 4 tests): default = unload the four daemons (VMs before their uplink), remove their plists and the launcher; **keeps** VM disks, backups, logs, account, socket_vmnet and the firewall. Opt-in flags: `--purge-data`, `--purge-socket-vmnet`, `--remove-account` (refused while data remains), `--restore-firewall`. It touches nothing of other projects and refuses protected delete targets.
Recovery procedures: (a) **host reboot:** daemons load at boot; with FileVault off the VMs start unattended (see D-F); verify with the reboot acceptance tests R1-R10 (`INDEPENDENT-REVIEW-CHECKLIST.md`). (b) **VM disk corruption:** stop the daemon, restore the qcow2 from the last good copy or re-create from the seed (VMs are rebuildable: state = PostgreSQL dump + config); the paper database is restored from the age-encrypted dump with the offline identity (`db_restore_verify.sh`, rehearsal `backup-key-rehearsal.sh`). (c) **crash-looping daemon:** `launchctl bootout system/com.asta.vm.<x>`, read `/var/log/asta/*.err`, fix, `launchctl bootstrap`; the firewall VM down means **no egress at all** (fail closed). (d) **socket_vmnet failure:** firewall VM loses its uplink: fail closed; restart the root daemon. (e) **lost the offline backup key:** encrypted backups are unrecoverable; rebuild from the live database and rotate (`VAULT-AND-BACKUP.md`). (f) **full rollback:** `rollback-host.sh --apply` then, if wanted, `--purge-data --remove-account`.

## 8. What this audit does and does not establish
Established: the host's actual capacity and posture; ARM64 compatibility of everything ASTA runs; the exact effects of provisioning; a safe rollback. **Not established:** that the boundary works on this Mac (needs `sudo` provisioning + the acceptance suite), that the host is isolated from unrelated workloads (it is not, H-1), or anything about the VMs' runtime behaviour on this hardware. Design validation (nft syntax, namespace lab, Docker gates) does not prove deployed host isolation.
