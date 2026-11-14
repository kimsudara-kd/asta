# Security-event routing, bridge supervision deployment and independent off-host alerts (ASTA-M3B-R4, workstream E)

Design plus the offline pieces that were implemented and tested. No alert channel, network destination, credential or secret was configured or contacted.

## 1. Event catalogue: what must reach the append-only audit chain
| Event | Source | Severity | In the audit chain now? | Off-host alert? |
|---|---|---|---|---|
| kill switch tripped (any cause) | `trip_kill_switch()` via bridge, reconciler, fill ingest, manual | critical | **yes** (`killswitch.trip`, cause + reason) | yes |
| kill switch released / **release refused** | `release_kill_switch()` (identity = `session_user`) | critical / warning | **yes** (`killswitch.release`, `killswitch.release_refused`, with the authenticated login) | yes |
| risk-halt bridge trip (once per halt event) | `risk_bridge` | critical | **yes** (actor `risk-bridge`) | yes |
| **bridge heartbeat missing/stale, database unreadable, latch pending** | `orders/monitor.py` every 30 s | critical / warning | **yes, new in R4**: `security.alert_state` appended **only when the set of active alerts changes** (entering and leaving), codes + severities, no secrets | yes |
| fill conflict / rejected fill (overfill, out of limit, bad state) | `order_fill_conflicts` (append-only table) + trip | critical | via the trip (`FILL_CONFLICT`/`FILL_REJECTED`) | yes |
| foreign order at the exchange / reconciliation mismatch | `Reconciler` -> trip | critical | via the trip | yes |
| channel refusals (NOT_AUTHENTIC, REPLAYED, KEY_NOT_ALLOWED...) | `Receiver.open` | warning (burst = critical) | **not yet** (reason codes returned, not persisted) | on burst |
| egress denials, allowlist/policy change, `resolves-to-non-public` | firewall VM proxy log / nftables counters | warning / critical | **not yet** (needs shipping, F-07) | yes |
| unit failure of `asta-fw`, `socket_vmnet`, VM daemons | launchd / systemd | critical | not yet | yes |
| vault: lease, refusal, policy change, unseal, rotation | vault audit device | warning / critical | not yet (no real vault) | yes |
| privileged host actions (provisioning, rollback, FileVault/firewall changes) | operator transcript | info | by transcript in the evidence folder | n/a |
Rule: an event is **routed** to the chain by writing through `append_audit` in the same transaction as the state change (trips, releases, bridge) or by a transition-only writer (monitor), never by free-form logging. Payloads carry ids, codes and counts, never secrets. The chain itself is verified in tests (`verify_audit_chain`) after every kind of event written in R3/R4.
**Not yet routed (design):** a *shipper* in the app VM tails (a) the proxy decision log, (b) nftables drop counters, (c) the channel refusal counters, (d) the vault audit device, and writes `security.event` rows through a dedicated audit-append role (INSERT-only on `audit_events` via a SECURITY DEFINER function so a compromised shipper cannot rewrite history; the hash chain stays computed in Python). Needs the VMs; tracked as F-07.

## 2. Independent off-host alerts (design; nothing contacted)
Goal: **independent** = still works when the whole Mac, a VM, the bridge, the database or the network path is down, and cannot be silenced by a compromised application VM. Two complementary channels:
1. **Dead-man's-switch heartbeat (detects silence):** a tiny job on the *firewall VM* (not the app VM) pings an external heartbeat monitor every minute **only if** (a) the app VM's monitor reports healthy (read over the fw<->app link) and (b) the fw rules hash equals the pinned value. If the Mac loses power, a VM dies, the network is cut or the job is stopped, **the external service notices the missing ping** and alerts the owner (push/email/Telegram) with no help from ASTA. Destination = one hostname added to the proxy allowlist by the normal signed two-reviewer change; the URL contains an unguessable id (a secret: stored root-only on the fw VM, never in git) and nothing else is sent.
2. **Push on events (detects bad news):** the monitor's critical state and every trip are posted from the firewall VM to a push endpoint (same allowlist mechanism). Content: event code + time + short id only.
| Option | Independence | Cost / privacy | Verdict |
|---|---|---|---|
| hosted dead-man's-switch service (healthchecks-style ping URL) + its email/push integrations | high (third-party) | free tier; the ping URL is a bearer secret; leaks reveal only that a host exists | **recommended for (1)** |
| self-hosted `ntfy` on another machine reachable over the tailnet | high if it is a different physical machine | operations burden; the Mac cannot reach the tailnet from inside the fw VM (egress is the allowlisted proxy only) | optional for (2) on another host |
| hosted push (ntfy.sh topic, Telegram bot) | medium-high | the topic/bot token is a secret; messages cross a third party | acceptable for (2) with minimal content |
| email via SMTP | high | needs SMTP credentials on the fw VM (a secret on the boundary host) | not recommended |
| the audit chain / journal / spool file alone | **none** (dies with the host) | | necessary but not sufficient |
| macOS host agent (launchd as `_astaalert`) reading VM status | partial (dies with the Mac) | | useful for local notifications only |
Requirements: the alert path must not hold any exchange credential; it must not be reachable from the app VM except as a one-way status feed; a test (acceptance, with the real VMs) must **kill** each component in turn (bridge, DB, app VM, fw VM, power) and show the owner's phone alerting within the target time (suggested: 2 minutes for silence, 30 s for a trip).

## 3. Bridge supervision deployment design (completes R-D1b)
Implemented and tested offline: units (`asta-risk-bridge.service`: `Restart=always`, never gives up, hardened, no network; `asta-security-monitor.service/.timer` every 30 s with `OnFailure` alert; `asta-security-alert@.service`), the heartbeat and `BridgedKillState`, the least-privilege roles and peer-authentication entries, and the installer `infra/security-host/mac/app-vm/install-security-services.sh` (runs **inside the app VM** as root, dry-run by default, refuses on macOS, creates `asta-bridge` and `asta-monitor` with no shell/home/password, writes unix-socket-only environment files without a password, applies `pg_roles_m3b.sql`, installs and enables the units, prints the verification steps). Order: PostgreSQL migrated to head (0011) -> roles + `pg_hba`/`pg_ident` -> units. Verification in the VM: bridge active; monitor exit 0; **kill the bridge: the gate closes within 30 s, the monitor reports `BRIDGE_HEARTBEAT_STALE` (exit 2), exactly one `security.alert_state` audit event appears, systemd restarts the bridge, the alert clears with a second event**. Requirement: any process that dispatches (the future order worker) **must be constructed with `BridgedKillState`**, never a bare `SqlKillSwitch`; an architecture test already pins that nothing outside `orders` builds a dispatcher (`tests/orders/test_gateway_boundary.py`); using `BridgedKillState` at the future construction site is a review item of the connector change and should get its own test then.
Still open: the `asta_orders` role/unit is not installed (no order path); the installer has not been run (the VM does not exist); `pg8000` unix-socket URL form (`?unix_sock=`) is asserted from the library's connection parameters and must be confirmed on the VM.
