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


---

# R2: finalized Linux security-host deployment plan (M3A-R2 Workstream B)

Status: **design and configuration artifacts only, nothing deployed, nothing on this PC changed** (no Windows firewall, hypervisor, security setting or shared Docker configuration was touched; M2 operations unchanged). F-02 remains OPEN until the dedicated host exists and is independently verified. All artifacts are in `infra/security-host/` and checked by `tests/m3a/test_security_host_config.py` (12 tests, including `nft -c` syntax validation of both rule sets in a scratch container that loads nothing).

## R2-1. Topology and boundaries

```
                       +--------------------- Linux security host (dedicated machine or hypervisor) --------------------+
 Windows PC (M2,       |                                                                                              |
 untouched)            |   asta-rt  (runtime VM)            asta-prot (private L2 switch,        asta-fw (firewall VM)    |
 no path in/out of  X  |   10.77.0.10/24                    10.77.0.0/24, NO uplink,             10.77.0.1 (prot0)        |    Internet
 the boundary          |   connector + vault (unix sock)    no host adapter)                     up0 = ONLY uplink  ----- |----> allowlisted public
                       |   no route, no DNS, no IPv6  --------------prot0-------------------->  nft default-DROP, proxy    |      names/ports only
                       +----------------------------------------------------------------------------------------------+
```
Boundary 1 (hypervisor/virtual switch): `asta-prot` has no uplink and no host-side adapter, so the runtime VM cannot reach the Windows host, the LAN or the Internet by construction. Boundary 2 (guest nftables on `asta-rt`): output is default-DROP, only `10.77.0.1:3128`; IPv6 only loopback. Boundary 3 (`asta-fw`): input only `10.77.0.10 -> 3128`; forward DROP (it never routes); output only TCP 443/9443 to the resolved allowlist set plus root-only DNS/NTP to one pinned upstream. Boundary 4 (systemd): proxy and connector units add `IPAddressDeny=any` with explicit allows. Each layer is independently sufficient for the egress rule it states; a mistake in one is caught by the next.

Hard rules of the host: no shared folders/clipboard/USB, no nested virtualization, no SSH password login, no package manager access to the Internet except through the proxy during a change-controlled maintenance window (the allowlist would need the Ubuntu mirror hosts added by a reviewed change and removed afterwards), snapshots before every change, disks encrypted.

## R2-2. Independently enforced egress rules (summary; exact text in the files)
| File | Purpose |
|---|---|
| `firewall-vm/nftables.conf` | `inet asta_fw`: input/forward/output policy DROP; `iifname prot0 ip saddr 10.77.0.10 tcp dport 3128`; output `ip daddr @allowed_dst tcp dport {443, 9443}` on `up0`; DNS/NTP only with `meta skuid 0` to the pinned upstream; counters + rate-limited LOG on every drop |
| `firewall-vm/asta-allowlist-refresh.{sh,service,timer}` | every 60 s resolve allowlisted names (as root), admit only public unicast IPv4, update the nft set and write `/etc/asta-egress/hosts` atomically |
| `firewall-vm/asta-egress-proxy.service` | `infra/egress/proxy.py` as a `DynamicUser`, no capabilities, `BindReadOnlyPaths` hosts file + empty `resolv.conf` (the proxy has **no DNS egress**, so no tunnelling path), full sandboxing |
| `firewall-vm/netplan-50-asta.yaml` | `prot0` static `10.77.0.1/24`, `up0` the single uplink, no IPv6/RA, DHCP DNS ignored |
| `runtime-vm/nftables.conf` | `inet asta_rt`: input only from the admin jump on 22 (optional; console preferred), output only to `10.77.0.1:3128` |
| `runtime-vm/netplan-50-asta.yaml` | `prot0` static `10.77.0.10/24`, **no gateway, no nameservers, no IPv6** |

Routing: the runtime VM has no default route; its only on-link network is `10.77.0.0/24`. The firewall VM has `net.ipv4.ip_forward=0` and `net.ipv6.conf.all.disable_ipv6=1`; its default route is on `up0` for the proxy's own traffic only. Router-side (optional, separate approval): a LAN ACL allowing the firewall VM's MAC/IP only 443/9443 outbound and the pinned DNS/NTP.

## R2-3. Secrets-vault placement and privilege boundaries
* The vault runs on **asta-rt** as its own service user `asta-vault`, in a systemd unit with `PrivateNetwork=yes` and `RestrictAddressFamilies=AF_UNIX`: it has **no network at all**. Its only interface is the unix socket `/run/asta-vault/vault.sock`, mode 0660, group `asta-connector`; the vault authenticates the peer with `SO_PEERCRED` and applies the per-identity grants (signed two-reviewer configuration) before leasing.
* Three separate users: `asta-vault` (store + key), `asta-connector` (may connect to the socket and to the proxy), and `asta-app` (strategies, AI modules, UI, research: no socket permission, no route). A compromised strategy/AI process cannot read the socket or the vault state directory (`0700`, owned by `asta-vault`).
* Key custody for the encrypted store: the key-encryption key is a systemd encrypted credential (`LoadCredentialEncrypted`, bound to the VM's TPM/host key where available) or an offline-entered unlock at boot; **never in the repository, an environment file or the proxy VM**. Backups never contain the unlock secret (R2-4). The real backend choice (file store + KEK as above, or an OS keystore/HSM) is for the independent review; the contract (`asta.security.vault`) is backend-independent.
* The vault server module (`asta.security.vault_server`) and the connector (`asta.connectors.demo`) referenced in the unit files **do not exist yet** - they are M3-B work and explicitly not authorized; the units are design artifacts.
* Privilege boundary summary: no process on `asta-rt` runs as root except SSH/systemd; connector and vault have empty capability sets, `NoNewPrivileges`, syscall filters, no core dumps; the firewall VM's proxy has no capabilities and no DNS.

## R2-4. Backup, restore and operational monitoring
| Item | Plan |
|---|---|
| What is backed up | configuration in Git (the source of truth: nft files, units, allowlist, netplan); the encrypted vault store file(s); audit-log database dumps; VM disk snapshots (before changes) |
| What is never backed up | the vault unlock secret/KEK; any plaintext credential; the proxy hosts file (regenerated) |
| Encryption and custody | backups are encrypted (age/gpg) to an offline key held by two named custodians; stored off the host (offline media); integrity hash recorded in the audit chain |
| Schedule | configuration: every change; vault store: after every store/rotate/revoke; audit DB: daily; snapshot: before every change |
| Restore drill | quarterly on a scratch VM: restore config + vault store, unlock with the offline secret, verify with `negative_tests.py`/`gate_tests.py` adapted, verify the audit chain (`verify_chain`) and that a revoked credential stays revoked; record time-to-restore |
| Monitoring | proxy decision JSON log and nft drop counters shipped to the audit chain (finding F-07); alerts on: any DENY burst (>20/5 min), any `resolves-to-non-public`, nft policy change, allowlist set empty, refresh job failed > 3 intervals, unit failure, clock drift > 2 s, vault lease outside the expected connector/purpose, any `lease.denied`, disk > 80 % |
| Integrity | hash of `nftables.conf`, units and `allowlist.json` checked hourly against Git; drift alerts |
| Time | chrony pinned to the single upstream (output rule above) |

## R2-5. Migrating the existing M2 paper sessions later (not authorized now; M2 unchanged)
The M2 sessions and supervisor keep running on the Windows host exactly as they are. A later, separately authorized migration should (a) never carry credentials (the paper service has none), (b) run the paper service behind the same proxy only if a reviewer wants all egress uniform, and (c) keep the observation database where it is unless there is a reason to move it. If moved:
1. **Preconditions:** security host verified and reviewed (F-01/F-02/F-03 closed or accepted); written CEO approval; a backup of the database and `data/paper`; a rollback window defined.
2. **Rehearsal on a copy:** dump `asta` (pg_dump), restore into a scratch database on the new host, start the paper service in **observation-only/paused** mode against the copy, and prove: session ids and config hashes identical, every stored checkpoint digest verifies by replay (`scripts/paper_integrity.py`), reconciliation 0 discrepancies, daily reports reproduce (`verify_report`), frozen-evidence verifier green.
3. **Cutover:** stop the Windows supervisor task (recording the gap as unmonitored time), take a final dump, restore, run the same verification, start the service on the new host through the proxy, confirm both feeds fresh and steps continuing from the stored cursors with gap detection/backfill covering the cutover interval; sessions are **never recreated or reset** (a recreated session is a new evaluation window and must be disclosed as such).
4. **Rollback:** restart the Windows task against the untouched original database (kept read-only until the new host passes a defined soak period); any divergence is a no-go and is disclosed.
5. **Evidence:** before/after integrity JSON, hashes of the dumps, incident records, time of the unmonitored gap.

## R2-6. Remaining prerequisites to deploy (decisions, not code)
CEO choice of dedicated host technology and approval for the host changes it needs; hardware or VM software and administrator rights; the router/ISP DNS decision (the upstream currently sinkholes OKX, so OKX stays unavailable regardless); reviewer-set key ceremony; the independent reviewer. Until then F-02 and F-03 stay OPEN and credential readiness stays NO-GO.
