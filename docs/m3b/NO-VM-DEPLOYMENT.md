# Deployment without virtual machines (owner decision 2026-11-21: "I don't use VM")

**Status: DESIGN for owner review. Nothing here is applied.** It replaces the firewall-VM / app-VM / credential-VM topology
(`SECURITY-TOPOLOGY.md`, `infra/security-host/mac/*`, `HOST-HARDENING-RUNBOOK.md`) for the **Binance Spot Testnet, virtual-funds** scope.
Those VM artifacts stay in the repo as unused reference; the VM provisioning, VM reboot and VM-egress gates are re-scoped below.
The connector code (`proposals/binance_testnet_signer/`) is unaffected: it is a port that is injected with a signer and a transport.

## 1. Honest comparison
| Property | With VMs (old design) | Without VMs (this design) |
|---|---|---|
| Separate kernel for the key holder | yes | **no** — same macOS kernel and same machine |
| Key unreadable by the app | yes (other VM) | yes, by **OS user separation** (file mode 0400, user `asta-cred`) |
| Network egress forced through a separate firewall | yes (firewall VM) | **optional pf anchor** (privileged) + application allow-lists |
| Survives a root / admin compromise of the Mac | partly | **no** (anyone with admin can read everything) |
| Off-host liveness | dead-man script on the firewall VM | the monitor pings an **external heartbeat service**; silence ⇒ alert |
| Complexity / privileged steps | high | low |
**Conclusion:** acceptable for *virtual-fund testnet* keys (a stolen testnet key moves no real value). **Not** a basis for real money:
a real-money pilot would need a separate machine or a hardware-backed signer. That is a later, separate decision; finding F-02 stays OPEN for it.

## 2. Single-Mac layout
| Component | Runs as | Notes |
|---|---|---|
| API, dashboard, paper workers, dispatcher | your normal user (or a dedicated `asta` user) | no key, no signing code (`src/` is lock-tested) |
| Connector service (`BinanceTestnetPort` + signer) | **`asta-cred`**, a separate standard macOS user with no login, run by a `LaunchDaemon` | holds the testnet key in `~asta-cred/` (0700, key 0400) or its own keychain; speaks only on the socket below |
| IPC | Unix socket owned `asta-cred:asta-ipc` mode 0660; app user in group `asta-ipc` | the existing replay-protected `Envelope` channel on top; peer-credential check on connect |
| PostgreSQL | local only (127.0.0.1 / socket), roles as in `pg_roles_m3b.sql` (`asta_orders`, `asta_monitor`, `asta_release`, …) | peer auth maps to OS users where available; otherwise scram passwords in 0400 files per user |
| Monitor + off-host alerts | `asta-monitor` user or your user | pings the heartbeat URL directly (`Notifier`, https only, no redirects); extended checks unchanged |
| Egress limits | app-level: signer refuses non-testnet hosts, connector has no other transport; **optional** pf anchor: `asta-cred` may reach only `testnet.binance.vision:443`, app user may not | pf needs sudo, so it goes in the host-hardening list, not in the build |

## 3. What changes in the build (no change to the order-path safety logic)
1. The credential side becomes a **local service** (`connector/`, outside `src/`) instead of a VM process. The wire (`channel.Envelope`), the
   connector, the mock and the lock tests stay as designed.
2. `ExternalChannelPort` (in `src/asta/orders/`) talks to a **Unix socket** instead of a VM link; fail closed when the socket is absent.
3. Heartbeat/dead-man: `monitor.py` keeps the signed status stamp for local use and additionally pings the external service **only when healthy**;
   the service alerts when pings stop (this also covers "Mac off", "app hung", "monitor dead"). `asta-deadman.sh` is not used.
4. Enablement, kill switch, releaser identities, caps: unchanged (§4 of the connector proposal).

## 4. Gates re-scoped (pending the reviewer's agreement; nothing is silently closed)
| Gate | Old requirement | Re-scoped requirement for the no-VM testnet scope |
|---|---|---|
| I-1 dedicated host | dedicated/isolated Mac | Mac hardened (FileVault, firewall, no auto-login, SMB/SSH locked) and **no real-money keys on it**; unrelated services quit |
| I-2 independent egress boundary | firewall VM | user separation + (optional) pf anchor; app-level allow-lists tested; the Docker egress lab remains evidence of the proxy logic only |
| I-6 reboot behaviour | VM reboot test | launchd services (`asta-cred` connector, API, monitor) restart after reboot; FileVault prompt documented |
| E-9 supervised units | systemd in the app VM | launchd `KeepAlive` plists for the same processes |
| F-02 | no dedicated VM | stays OPEN as an **accepted risk for testnet only**; blocks any real-money step |

## 5. Privileged steps you would run (later, not now)
Create user `asta-cred` + group `asta-ipc`; install the connector LaunchDaemon; (optional) load a pf anchor for `asta-cred`. Exact commands will be
written when step B is approved, with rollback, like the host-hardening change set.
