# Dedicated VM / independent boundary: audit, design, requirements, deployment plan (M3A-R1 Workstream A)

**Status of finding F-02 (the boundary is not independently enforced): OPEN.** A dedicated VM could not be provisioned on this PC (section 2). What *was* built and verified is a stronger in-Docker boundary (kernel netfilter gate, section 4); it is **not** a substitute for a dedicated VM because it shares the Docker Desktop VM and kernel with every other project on this machine. No containment is claimed from application-level or proxy-code checks alone.

## 1. Audit: ASTA processes outside the protected boundary (2026-11-13, evidence `docs/evidence/m3a/r1-process-audit.txt`)

| Process (host, Windows) | Parent | Network behaviour | Inside boundary? |
|---|---|---|---|
| `cmd.exe /c scripts\run_paper_supervisor.cmd` (Task Scheduler `ASTA-Paper-Supervisor`) | Task Scheduler | none | no |
| `paper_supervisor.py` (launcher 35188 + interpreter 43108) | cmd | 1 localhost connection (PostgreSQL 127.0.0.1:5640) | no |
| `python -m asta.paper_service` (launcher 41952 + interpreter 46748) | supervisor | **direct outbound TLS to Binance public hosts** (observed `13.159.191.183`, `54.92.91.152`) + 4 localhost DB connections | **no** |
| Docker `asta-db` (127.0.0.1:5640), `asta-db-test` (127.0.0.1:5641) | Docker Desktop | localhost only | no (data stores, no egress needed) |
| API / web (`uvicorn`, Next.js) | started on demand for tests | localhost | no |

Consequences: the only process with Internet egress that is *not* in the boundary is the M2 paper service, and it carries **no credentials and no signing/order code** (live-lock tests). It must keep running unchanged during the observation (M2 operations are preserved; migration is not authorized). The rule for M3-B stands: **no credential-holding process may ever run outside the protected runtime.**

## 2. Why a dedicated VM was not provisioned here
Checked on this PC: Windows 11 **Home** (no Hyper-V manager, no Windows Sandbox), no VirtualBox / VMware / QEMU / Multipass / Vagrant installed, the user session has **no administrator rights**, Docker Desktop (WSL2 backend) and a stopped `Ubuntu-24.04` WSL distribution that belongs to other work. WSL2 distributions share one VM and one network namespace tree with Docker Desktop, so a "new WSL distro with a firewall" would affect every other project. Changing host firewall, network adapters or Hyper-V features is a shared-host change that the directive forbids without separate CEO approval. Therefore: verified architecture and deployment plan below, finding left OPEN.

## 3. Target architecture (dedicated VM or equivalent)

```
   Windows host (untouched)          Boundary VM "asta-guard" (independent kernel)                     Internet
   ---------------------------       --------------------------------------------------------      ----------
   paper service, DB, UI             [protected runtime VM]  --vSwitch "asta-prot" (private, no uplink)-->  [firewall/proxy VM]
   (no credentials)                   connector + vault        no host adapter, no NAT                       nftables default-deny,
                                      no default route                                                       proxy (CONNECT allowlist),
                                                                                                             allowlisted-name DNS, logs
                                                                                                             uplink only to the Internet NIC
```
Two independently controlled layers are preferred over one VM: (a) the protected-runtime VM has **no route out at all** (private virtual switch without an uplink, no host-only adapter exposing host services); (b) the firewall/proxy VM has the only uplink, with kernel rules identical in intent to `infra/egress/gate/entrypoint.sh` (default DROP in input/output/forward; outbound TCP only to resolved allowlisted public addresses and ports; IPv6 off; DNS only for allowlisted names). Hypervisor-level controls add: no shared folders, no clipboard/drag-drop, no USB/serial, no nested virtualization, snapshots before changes, VM disk encrypted, host firewall rule (separate CEO approval) that lets only the firewall VM's uplink reach the router.

Acceptable equivalents (any one, CEO choice): (1) Hyper-V VMs on a Windows Pro/Enterprise host; (2) VirtualBox or VMware Workstation Player (available on Windows Home via the Windows Hypervisor Platform) with the same two-VM layout; (3) a separate physical mini-PC / small single-board computer used as the firewall, with the runtime in a VM or on a second small machine behind it; (4) a cloud VM pair with security groups whose egress rules list only the allowlisted public address ranges (note: cloud security groups filter by IP, not by name; the guest-level nftables rule set is still required). Not acceptable: relying on Docker Desktop alone, Windows firewall rules on the shared Docker backend, or the application allowlist.

## 4. What was built and verified now (in-Docker, disposable, ASTA-only)

`infra/egress/` (version-controlled): internal network `asta_protected` (172.31.250.0/24, no gateway, no IPv6) -> `egress-gate` container (the only holder of `NET_ADMIN`, inside its own network namespace) with the **proxy running inside the gate's namespace** (`network_mode: service:egress-gate`, non-root, no capabilities) -> `asta_egress` NAT bridge (172.31.251.0/24). The gate installs, before anything else runs, nftables `policy drop` on input, output and forward (the `inet` family covers IPv6 too), then allows only: loopback; established flows; inbound TCP 3128 from the protected subnet; outbound TCP to addresses that the gate itself resolved for the allowlisted names (public unicast IPv4 only, refreshed every 60 s, 10 min expiry) on ports 443/9443. DNS is answered by a local dnsmasq from the gate-resolved list only (every other name gets REFUSED). The proxy's `resolv.conf` is a read-only mount pointing at that resolver, so there is no DNS tunnelling path.

This answers requirement 5 (restrict the proxy's outbound destinations independently of its code): a fully compromised proxy process still cannot connect to any other address or port, use UDP/DNS, or reach IPv6, because the kernel drops it (`docs/evidence/m3a/r1-gate-tests-*.json`: 39 attacker-style checks from an unprivileged process inside the proxy's namespace, drop counters in `r1-boundary-verification.json`).

Why this is still **not** the dedicated VM: it runs on the shared Docker Desktop kernel (a kernel/Docker vulnerability or a host-side Docker setting breaks it); the gate container holds `NET_ADMIN` (scoped to its own namespace) and shares the daemon with all projects; there is no hypervisor-level isolation and no independent host firewall rule; and the protected runtime's isolation is Docker's, not a VM's.

## 5. VM sizing and requirements (for the dedicated boundary)

| Item | Protected-runtime VM | Firewall/proxy VM |
|---|---|---|
| OS | Ubuntu Server 24.04 LTS minimal (no snap, no cloud-init agents with network side effects) | Ubuntu Server 24.04 LTS minimal or Alpine 3.20 |
| vCPU | 2 | 1 |
| RAM | 4 GB (connector + vault process + Python runtime; Postgres stays on the host/another tier) | 1 GB |
| Disk | 30 GB thin, encrypted (logs, vault store, images) | 8 GB thin |
| NICs | 1 x private virtual switch `asta-prot` (no uplink), static address | 2: `asta-prot` + uplink (bridged/NAT to router) |
| Services | connector, vault process, node exporter-free (no extra listeners) | nftables, CONNECT proxy, allowlist DNS, journald forwarding of drop logs |
| Hardening | non-root service users, `ufw`/nftables default deny, unattended security updates via the firewall VM's allowlist window only (change-controlled), AppArmor profiles, no SSH password auth (console or key via the private switch from an admin jump only) | same |
| Host impact | none beyond virtual switch/adapter creation (requires CEO approval; admin rights) | none |
| Host resources reserved | about 3 vCPU, 5 GB RAM, 38 GB disk of this PC's 24 threads / 32 GB / E: 156 GB free | |

## 6. Deployment plan (requires separate CEO approval for host changes)
1. Approve a VM technology and provision rights (administrator session once; record every host change).
2. Create the private switch `asta-prot` and the two VMs from a verified ISO (checksum/signature recorded); snapshot "clean".
3. Firewall VM: install the nftables ruleset and the proxy from this repository (`infra/egress/gate/entrypoint.sh`, `proxy.py`, `allowlist.json`, hashes logged); restrict its uplink to outbound TCP 443/9443 to resolved allowlisted addresses.
4. Runtime VM: no uplink; default route absent; resolver = firewall VM allowlist DNS only.
5. Run the verification suites adapted to the VM layout (all of: `negative_tests.py` from the runtime VM, `gate_tests.py` as an unprivileged process on the firewall VM, plus hypervisor-level checks: no shared folders/clipboard, switch has no uplink, MAC/IP spoofing attempts, `ip route`/`ip -6` inspection, packet capture on the uplink showing only allowlisted flows).
6. Restart matrix: process restart, service restart, VM guest reboot, hypervisor host reboot, snapshot revert, firewall VM stopped (runtime must have zero connectivity), runtime VM compromised simulation. Record each result.
7. Independent reviewer re-runs the matrix; findings closed per `THREAT-MODEL.md`.
8. Only then (and with separate authorization) migrate any process that will hold credentials. The legacy M2 host processes stay where they are until a separately authorized, verified migration.
Rollback: delete the VMs and switch; snapshots before each change; nothing on the host depends on them.

## 7. Restart and failure behaviour verified now (in-Docker gate)
`docs/evidence/m3a/r1-boundary-verification.json` (`python infra/egress/verify_boundary.py`): baseline, after proxy restart, after gate restart (dependents recreated), and after recovery all pass 78/78 negative checks and 39/39 compromised-proxy checks; with the **gate stopped**, the protected runtime has zero connectivity (alias unresolvable, no route) - fail closed; recovery restores service. **Docker Desktop/VM restart and Windows reboot were not performed**: that would interrupt every other project and the M2 database container; recorded UNVERIFIED (F-14).
