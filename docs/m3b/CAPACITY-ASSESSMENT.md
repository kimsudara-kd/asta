# Storage and RAM capacity assessment and two-VM validation plan (ASTA-M3B-R4, workstream C)

Measured on this Mac mini M4 (16 GiB, 228 GiB internal SSD) on 2026-11-14. Load tests ran two scratch QEMU/HVF VMs as an ordinary user (no `sudo`, no host change) from copy-on-write overlays of an Ubuntu 24.04 arm64 image; both were stopped afterwards and only the overlays remain in the session scratch directory. **These are measurements of a scratch environment on a busy, shared host, not of the deployed system.**

## 1. Storage (actual)
| Item | Value |
|---|---|
| Internal APFS container | 245 GB; Data volume 228 GiB: **145 GiB used, 57 GiB free** (it was 60 GiB earlier today: my scratch VM overlays and pulled images) |
| Docker Desktop data disk (`Docker.raw`) | **50.6 GiB allocated** (apparent 228 GiB, sparse); images 15.1 GB, volumes 7.06 GB, build cache 4.4 GB, containers 75 MB |
| `~/Library/Application Support` / `Caches` | 23.6 GiB / 3.9 GiB |
| External "Dr. Sudara" (USB, exFAT, noowners) | 3.6 TB, 3.1 TB free, 571 GB used: **fine for age-encrypted backups, forbidden for VM disks** (no ownership) |
| Session scratch (base image + overlays) | 3.7 GB (can be deleted) |

## 2. Budget for the two-VM phase (thin disks; numbers are maximums to reserve, with a realistic steady state)
| Component | Reserve (max) | Realistic steady state | Notes |
|---|---|---|---|
| `asta-fw` disk | 8 GB | ~3 GB | Ubuntu minimal + nftables + proxy + logs |
| `asta-app` disk | 40 GB | ~8-12 GB | OS 3 GB; PostgreSQL (the M2 observation database dumped to ~0.9 MB/day); paper/research data; logs |
| Base cloud image | 0.7 GB | 0.7 GB | read-only backing file |
| qcow2 snapshots (2 on `asta-app`, 1 on `asta-fw`) | 10 GB | 2-5 GB | copy-on-write grows with changes; drop old ones |
| Encrypted-backup image | 20 GB raw (sparse) | < 1 GB | holds only age-encrypted dumps (14 dailies); **copies go to the external drive (4 retained)** |
| Host logs (`/var/log/asta`, launchd) | 2 GB | < 0.2 GB | add a `newsyslog` rotation entry at provisioning |
| VM journals | 0.5 GB per VM | < 0.1 GB | capped by journald |
| Update reserve (guest security updates staged via the payload disk; QEMU/Homebrew upgrades with pin regeneration) | 4 GB | 1 GB | |
| macOS update reserve | 25-30 GB free at all times | | macOS major updates need this on the internal volume |
| **ASTA total (reserve)** | **~85 GB** | **~20-25 GB** | |
Verdict: **does not fit on the internal disk as the host is used today** (57 GiB free minus the 25-30 GiB macOS reserve leaves ~27 GiB, below the 85 GB reserve and tight even for the realistic 25 GB). It fits comfortably after the unrelated Docker projects leave (**+50.6 GiB**: ~108 GiB free, ASTA quota 60 GiB, macOS reserve 30 GiB, headroom ~18 GiB) or on a **separate APFS volume** with ownership enforced. Recommended: add an APFS volume in the internal container with a **quota of 60 GiB** and a **reserve of 30 GiB** (`diskutil apfs addVolume disk3 APFS ASTA-VM -quota 60g -reserve 30g`, a privileged step for the execution window, mounted at `/var/lib/asta-vm` via `--vm-base`): APFS volumes enforce ownership, a quota stops runaway VM growth from starving macOS, a reserve stops other software from starving ASTA. FileVault covers the whole container. Fallback: an external APFS-formatted drive (the 4 TB exFAT drive would have to be reformatted: not proposed).

## 3. RAM (measured)
| Measure | Value |
|---|---|
| Installed / macOS-reported free | 16 GiB / 48 % at the start, with **6.0-6.5 GiB held by the memory compressor** and 748,400 cumulative page-outs since boot: the host is already memory-contended |
| Docker Desktop VM | 7.75 GiB limit (allocated by Docker, used on demand) |
| Proposed guests | `asta-fw` 768 MiB + `asta-app` 3,072 MiB = 3.84 GiB, plus QEMU overhead (~0.3 GiB) |
| Design before R4 | fw 1 + app 5 + cred 2 = 8 GiB |
After dedicating the host (Docker, EXO, browsers gone), the estimate is macOS ~3.5-4 GiB + ASTA dev stack ~0.6 GiB + guests ~4.1 GiB = **~9 GiB of 16 (~44 % headroom)**; the credential VM adds 1 GiB later.

## 4. Two-VM load test (executed)
Setup: `app` 3 vCPU / 3,072 MiB and `fw` 1 vCPU / 768 MiB scratch VMs (HVF, user-mode NAT, no host share). Load for 60 s: in `app` three CPU-bound processes plus a **2.2 GiB resident** allocation (PostgreSQL + services working set stress); in `fw` one CPU-bound process plus a 400 MiB allocation. Host sampled every 3 s (swap, page-outs, compressor, free %, latency of the local dashboard).
| | Run 1 (cold) | Run 2 (saved: `docs/evidence/m3a/r4-two-vm-load-test-macos.json`) |
|---|---|---|
| macOS "free %" min (baseline) | 34 % (48 %) | 35 % (45 %) |
| compressor peak | 8.05 GiB (from 6.98) | 7.99 GiB (from 6.43) |
| page-outs during the load | +12,537 (~196 MiB) | +459 |
| swap-outs during the load | +3,784 pages (~59 MiB); swap used 38 -> 97 MB of 1 GB | 0 |
| dashboard (loopback HTTP) latency | max 10 ms (baseline 4) | max 4 ms (baseline 2) |
| guests | both completed their loads, no OOM kill; `app` used 279 MiB afterwards, `fw` 243 MiB | same |
**Reading:** the two-VM architecture runs under this load without failure on the contended host, at the cost of heavier compression and a little swapping on a cold start; it is **not** evidence about steady-state 24/7 behaviour or about the real services (PostgreSQL, paper service, proxy), which are not yet deployed. With the host dedicated, the headroom grows by the Docker VM's footprint.

## 5. Firewall enforcement independent of application code, and bypass tests (executed in the scratch VM)
`infra/security-host/acceptance/netns_lab.py` (extended in R4) builds `asta-fw` / `asta-app` / `asta-cred` network namespaces with the **unmodified** nftables files of the Mac design on a real aarch64 Linux 6.8 kernel, with the worst case enabled (IP forwarding on in `asta-fw`, default routes of app/cred pointing at it). **38/38 checks passed** (`docs/evidence/m3a/r4-netns-lab-aarch64.json`): the 22 earlier checks plus 16 new bypass checks - the app has exactly `lo` and `fw0` (no second NIC), no global IPv6; IPv6 to two public resolvers and to the firewall's link-local address blocked; cloud metadata (169.254.169.254), RFC1918 (10/8, 172.16/12, 192.168/16) and CGNAT (100.64/10) blocked; DNS over TCP 53, DoT 853, HTTP 80 and UDP/443 (QUIC) blocked; extra on-link /32, low-metric default and policy-rule **alternate routes** do not open anything; the app's own drop counters prove the rule (not luck) blocked the attempts; with the app's firewall flushed (compromised app) `asta-fw` still blocks; with `asta-fw` gone there is no connectivity (fail closed). **Independence from application code:** the decision is made by kernel nftables in a different machine from the application; the proxy (the only allowed path) runs unprivileged inside `asta-fw` and cannot change the rules (the namespace lab's "unprivileged user" check). Static tests (`test_mac_deployment.py`) additionally assert that **no VM has a host-network device**: the app VM has exactly one NIC, a unix-socket stream to the firewall VM; no `hostfwd`, `-nic`, `vmnet`, `tap`, user-mode NAT, `-virtfs`, USB or VNC on any VM; the firewall VM's only uplink is the root `socket_vmnet`.
**Not covered (needs the provisioned host):** the QEMU `stream` links, `socket_vmnet`, the hypervisor boundary, the macOS firewall, packet capture on `en0` (H7), and the real proxy under the real allowlist.

## 6. Validation plan for the deployed two-VM architecture (after provisioning approval)
1. `qemu-vms.sh prepare` (disks on the quota'd APFS volume), start `fw` then `app`; confirm sizes, the QEMU pin check, and that the cred VM refuses to start.
2. Run `vm_acceptance.py` pre-phase in each VM, the 78-check negative suite from `asta-app` through the proxy, the compromised-proxy checks in `asta-fw`, and H1-H8 on the host.
3. Re-run the bypass checks above **inside the real VMs** (not namespaces): IPv6, metadata, RFC1918, DoT, QUIC, alternate routes; capture on `en0` for the duration.
4. Load: real PostgreSQL + paper service + bridge + monitor under 3 vCPU / 3 GiB for 24 h while sampling swap/compressor and the heartbeat age; no heartbeat gap > 10 s, no OOM, host dashboard latency unchanged.
5. Reboot persistence: guest reboot, host reboot (planned and power loss), snapshot revert, `fw` stopped (zero connectivity), recovery without manual steps (C7, C8, R1-R10, B1-B10).
6. Disk: after a week check the growth of `asta-app`, snapshots and logs against section 2.
