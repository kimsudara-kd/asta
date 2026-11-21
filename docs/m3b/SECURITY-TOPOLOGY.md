# Final security topology, accounts and bridge supervision (ASTA-M3B-R3, workstream B)

External execution stays **disabled** throughout: no connector, no credential, no signing, `EXTERNAL` order surface = constant DENY, `ExecutionGateway` DENY. Nothing here enables any of it.

## 1. Reassessment against the 16 GiB limit
The three-VM design (fw 1 GiB + app 5 GiB + cred 2 GiB = 8 GiB, `MAC-MINI-DEPLOYMENT-DESIGN.md`) assumed a dedicated Mac. The inspected host is shared and already memory-contended (`MAC-READINESS-AUDIT.md` s.1, s.4). Requirement that must survive any shrinking: **independently enforced network isolation** = the egress decision is made by a kernel firewall in a machine that no application code and no credential holder can reconfigure.

| Option | RAM | Preserves independent enforcement? | Verdict |
|---|---|---|---|
| A. three VMs as designed | 8 GiB | yes | too large for this host as it is used today |
| B. **fw + app now; cred added at M3-B** | **~3.8 GiB now, ~4.8 GiB later** | **yes**: the firewall VM is separate from both the application and (later) the credential holder | **recommended** |
| C. fw + (app+cred merged) | ~4.5 GiB | partly: the credential holder shares a kernel with the application (F-01 strongest form lost) | rejected |
| D. one VM + host `pf` | ~3 GiB | no: host `pf` is administered by the same `apple` account and cannot be audited independently of the app VM's host | rejected |
Why B is the smallest safe architecture: until M3-B is authorised **no credential exists**, so a credential VM would hold nothing; the app VM already has no route to it in the design (app<->cred link not attached). Dropping it now removes 2 GiB without weakening any isolation that is currently needed. Proposed sizes (estimates, to be measured in Phase C): `asta-fw` 1 vCPU / 768 MiB / 8 GB (nftables + the allowlist proxy); `asta-app` 3 vCPU / 3 GiB / 40 GB (PostgreSQL ~128 MiB shared buffers, paper service, API, supervisor, bridge, monitor); `asta-cred` (later) 1 vCPU / 1 GiB / 10 GB (vault + connector only). Disks on an **external APFS volume with ownership enforced** (60 GiB free internally is too tight); the encrypted-backup image may sit on exFAT.

```
 Mac mini M4 (macOS 26, admin account `apple` = host trust base, F-19)
   en0 ──(root socket_vmnet, bridged, socket group _astavm)──► asta-fw  [only uplink; nftables default DROP; allowlist proxy; never forwards]
                                                                   │ unix-socket L2 (10.77.1.0/30; exists on no host interface)
                                                                   ▼
                                                              asta-app  [PostgreSQL (unix socket, peer auth), paper service, API,
                                                                         risk-halt bridge, security monitor, backups]
   (M3-B only, after separate authorisation)  asta-fw ──(10.77.2.0/30)── asta-cred [vault + connector]; app<->cred link stays detached
```

## 2. Accounts and privilege separation
| Where | Account | Purpose | Privilege |
|---|---|---|---|
| macOS | `apple` | owner, admin | host trust base (F-19); runs nothing of ASTA's boundary after provisioning |
| macOS | `root` | `socket_vmnet` only | bridged vmnet helper, group-restricted socket |
| macOS | `_astavm` | runs the QEMU VM daemons | no login, no password (`Password '*'`), no home; owns `/var/lib/asta-vm` (0700); cannot write its own launcher |
| asta-app | `postgres` | database server | peer auth, no TCP, no password |
| asta-app | `asta-app` | paper service, API, supervisor | role `asta` (owner today); egress only via the proxy |
| asta-app | `asta-bridge` | risk-halt bridge | DB role **`asta_bridge`**: read paper halt state; `EXECUTE trip_kill_switch`, `bridge_heartbeat`; write its seen-table and audit rows; **no** release, **no** halt-table writes |
| asta-app | `asta-orders` | order-path worker (dispatcher, fill ingest, reconciler); **not running** in this build | DB role `asta_orders`: intents/fills/replay state, may trip, **cannot** release |
| asta-app | `asta-monitor` | failure monitor | DB role `asta_monitor`: read-only heartbeat and latch |
| asta-app | `asta-release` | **interactive operator login, never a service** | DB role `asta_release`: `release_kill_switch()` only, and only for actors listed in `kill_switch_releasers` (empty by default) |
Enforced by: `pg_roles_m3b.sql` (tested on PostgreSQL: `tests/integration/test_bridge_supervision.py`, role matrix), `pg_hba.conf.asta` / `pg_ident.conf.asta` (peer only, distinct OS user per role), migration 0010 (`SECURITY DEFINER`, `search_path` pinned, `EXECUTE` revoked from PUBLIC), systemd hardening per unit. **Finding G-10 (Med, open):** inside `release_kill_switch` the *actor name is a parameter* checked against the listed releasers; authentication of the person is the DB login (peer map -> `asta_release`) plus the actor name, so a role-holder could pass another listed name. Closure: derive the actor from `session_user` (one DB login per releaser) and drop the parameter; recorded for the next authorised change. **G-11 (Low, open):** the latch-clearing guard also honours a custom setting; harmless without `UPDATE` on `kill_switch_state`, which no service role has, but the owner role can bypass it by design.

## 3. Supervised start-up of the risk-halt bridge
* `asta-risk-bridge.service` (app VM, user `asta-bridge`): `Restart=always`, `RestartSec=2`, `StartLimitIntervalSec=0` (never stops retrying), (no `WatchdogSec`: the bridge does not speak sd_notify; liveness is the DB heartbeat), `After/Requires=postgresql.service`, no network (`PrivateNetwork=yes`, `AF_UNIX` only), empty capability set, `NoNewPrivileges`, strict filesystem and syscall filters (tested like the other units).
* The bridge polls every 5 s and **beats a database heartbeat on every successful poll** (`bridge_heartbeat()`).
* **Fail closed on the bridge's own death:** the order path is built with `BridgedKillState` (latched OR heartbeat missing/stale > 30 s OR database unreadable = halted). A dead bridge therefore closes the gate within 30 s without any human action, and reopens it by itself when a restarted bridge beats (a mere outage needs no release; a *latched* halt still needs a human).
* Start order after a reboot: network/nftables -> PostgreSQL -> bridge -> (paper service) ; the heartbeat starts stale, so the order path is closed until the bridge has proved itself alive.

## 4. Failure monitoring of security-critical services
`asta-security-monitor.timer` runs `python -m asta.orders.monitor` every 30 s as `asta-monitor` (read-only). Exit 0 ok, 1 warning (kill switch latched: a human release is pending), 2 critical (bridge heartbeat missing/stale, database unreadable) -> the unit fails -> `OnFailure=asta-security-alert@%n.service` writes a CRITICAL journal line and an alert-spool line. Also required (from `VM-BOUNDARY-DESIGN.md` R2-4, F-07, not yet implemented): nftables drop-counter bursts, proxy `resolves-to-non-public` / policy-change / empty allowlist events, unit failures of the firewall VM and `socket_vmnet`, clock drift, disk > 80 %, shipping of the proxy and vault logs into the audit chain, and an alert channel that reaches a human off-box (the spool/journal alone is not one). Host side: `launchd` `KeepAlive` already restarts the four daemons; their logs are in `/var/log/asta`.
| Failure | Effect on safety | Detection | Recovery |
|---|---|---|---|
| bridge process dies / hangs | gate closes within 30 s (fail closed) | monitor critical, journal | systemd restarts it; heartbeat resumes; no human needed |
| database unreadable | gate closed; bridge cannot trip but nothing can dispatch | monitor critical | restore database; heartbeat resumes |
| paper session halts | bridge latches the kill switch (once per event) | monitor warning (latched) | human `release_kill_switch()` after review |
| firewall VM down / `socket_vmnet` down | **no egress at all** (fail closed) | host daemon logs; app proxy errors | restart the daemon |
| host reboot | all services restart; heartbeat stale until the bridge beats | monitor | automatic (see s.5 for FileVault) |

## 5. Host-restart persistence requirements
1. `pmset autorestart 1` (present) so power loss restarts the Mac; sleep disabled (`sleep 0`).
2. LaunchDaemons in the **system** domain start without a login. **FileVault (D-F):** with FileVault on, the data volume is locked until a human unlocks after every reboot, so VMs cannot start unattended; with it off (today), the boot is unattended but VM disks (PostgreSQL data) are readable from the unencrypted SSD. Recommendation: Phase 1 (no credentials) may keep it off *only if the owner accepts this*; **turn FileVault on before any credential exists** and accept a manual unlock after power events (the order path stays closed meanwhile, which is the safe direction).
3. VM disks live on an APFS volume with ownership enforced; that volume must be mounted before the daemons start (internal volume, or an external volume whose absence makes `qemu-vms.sh` fail and `KeepAlive` retry).
4. Every service in the VMs is a systemd unit enabled at boot; `nftables.service` before network-facing units.
5. After any reboot the **reboot acceptance tests R1-R10** (`INDEPENDENT-REVIEW-CHECKLIST.md`) must pass; the paper supervisor reconciles and records the gap as unmonitored time.
6. A Docker Desktop restart must not affect ASTA's VMs (separate hypervisor user, separate disks), and ASTA must not depend on Docker at runtime.
