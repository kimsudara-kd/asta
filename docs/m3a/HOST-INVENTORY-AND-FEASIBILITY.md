# Actual host inventory and dedicated-host feasibility (M3A-R3, read-only inspection 2026-11-13)

Inspection used `Get-CimInstance`/`Get-NetAdapter`/`Get-PSDrive` and tool lookups only; nothing was installed, enabled, partitioned or changed. Raw facts below; the conclusion is in section 4.

## 1. What actually exists
| Item | Fact |
|---|---|
| Machine | ASUS ROG Zephyrus G14 (GA403WM), a **single laptop** that is also the developer workstation and runs the M2 observation and every other project's containers |
| OS | Windows 11 **Home** Single Language 10.0.26200 (64-bit) |
| CPU | AMD Ryzen AI 9 HX 370, 12 cores / 24 threads, virtualization enabled in firmware; a hypervisor is present (the one used by WSL2/Docker Desktop) |
| RAM | 31.1 GB total; **4.5 GB free at inspection** (Docker Desktop VM, IDE, browsers, other projects' containers) |
| Storage | one 954 GB NVMe: C: 428 GB used / 224 GB free; E: 145 GB used / 155 GB free |
| Network | one usable physical uplink: Wi-Fi 7 (573 Mbps); no second NIC; Bluetooth PAN disconnected; a Hyper-V "FSE HostVnic" container adapter (Docker/WSL); Wi-Fi 3/5 not present |
| Administrator rights | **No** (the user session is not elevated) |
| Hypervisor tooling | none usable: no Hyper-V manager/Windows Sandbox (Home edition), no VirtualBox, VMware, QEMU, Multipass, Vagrant |
| WSL | `docker-desktop` (used by Docker Desktop) and `Ubuntu-24.04` (belongs to other work, shared) |
| Other machines | none known/available to this session |

## 2. Design requirements (from `VM-BOUNDARY-DESIGN.md`) versus the actual hardware
| Requirement | Verdict on this laptop |
|---|---|
| Separate kernel per boundary (VM) | possible only with a hypervisor that is **not** installed and **not authorized** to install on this PC |
| Private virtual switch without uplink + a firewall VM that alone has the uplink | would need an administrator-created virtual switch and a bridged/NAT uplink on the single Wi-Fi adapter (Wi-Fi cannot be bridged reliably by most hypervisors); the Windows host would remain adjacent to the VMs |
| Resources: about 3 vCPU, 5 GB RAM, 38 GB disk | fits the totals (24 threads, 31 GB, 155 GB free) but **not the current free RAM** (4.5 GB) without starving the M2 observation and other projects |
| Independence: a compromise of the host must not defeat the boundary | **not achievable on the same machine**: the Windows host owns the hypervisor, the disk and the only NIC; an administrator-level compromise of the PC defeats every VM boundary on it |
| Egress enforced by a device the runtime cannot reach | needs a separate router/firewall or a separate physical host |
| Reboot/recovery test of the boundary host without disturbing other work | rebooting this laptop interrupts the M2 observation and every project |

## 3. Options (all require a CEO decision; none was started)
| Option | What it is | Closes F-02 independently? | Cost/authorization |
|---|---|---|---|
| A. **Separate small Linux host (recommended minimum)** | a mini-PC or similar (>= 4 cores, 8 GB RAM, 128 GB SSD, **2 Ethernet NICs**) running Ubuntu 24.04 with the firewall role (`infra/security-host/firewall-vm`) and, behind it, either the runtime role on the same box in a VM (KVM) or on a second small box; uplink on NIC 1, protected segment on NIC 2 (no uplink) | **Yes** (independent kernel, NIC and power state; compromise of the Windows PC does not weaken it) | hardware purchase (**not authorized**) |
| B. Two VMs on this laptop (Hyper-V needs Pro; VirtualBox/VMware Workstation Player works on Home via Windows Hypervisor Platform) | the plan already specified | **No** - same machine, same owner of the disk/NIC; strictly better than Docker-only but still shares the failure domain; also requires installing a hypervisor and an administrator session (**not authorized**) | software install + admin rights (**not authorized**) |
| C. Cloud VM pair with security groups | two small VMs in a private network, egress by provider security group (IP-based) + guest nftables | Yes (independent), but the platform and provider become part of the trust base, and security groups filter by IP not by name | account, spend, data-handling decision |
| D. Status quo (Docker gate on this laptop) | built and verified in R1/R2 | No | none |

Recommendation: **Option A** as the smallest configuration that genuinely satisfies F-02 and F-01 (a credential-bearing runtime that no Windows-host process can reach). Option B is acceptable only as an explicitly accepted interim by the independent reviewer, never as the closure of F-02.

## 4. Conclusion
**No dedicated infrastructure is available**, and the authorizations needed to create it (hypervisor installation on this PC, hardware purchase) are expressly withheld. The design has therefore been validated against the *actual* hardware on paper, and it is **not deployable here without violating the restrictions**. R3 delivers everything needed to deploy on dedicated infrastructure the moment it exists (reproducible seeds, hypervisor scripts, deploy/rollback, acceptance suite - see `VM-BOUNDARY-DESIGN.md` R3), and **F-02 remains OPEN**.
