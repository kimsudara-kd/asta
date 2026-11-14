# ASTA-M3B-R6 — Security Recovery, Repository Hygiene & Host Dedication

Baseline 9e1f552 (+ c4b40dd test typing fix). Status: **BUILT — NOT ACCEPTED. Credential readiness: NO-GO.**
Scope of this round: controlled recovery *preparation* only. Nothing was shut down, deleted, rewritten, pushed,
reconfigured on the router, or run with sudo. Project names, addresses and the public IP appear only as aliases
(P1–P8, `<LAN>`, `<WAN>`); the key lives in `~/asta-private/r5/alias-map.md` (0700, outside the repository).

## 1. Docker containment readiness (C4 — conditional)

C4 = quit Docker Desktop. It is the preferred containment *only after* the dependency check below and an explicit owner
approval. **It was not executed.**

| Item | Finding |
|---|---|
| What stops | All 8 unrelated projects' containers; P3 (`0.0.0.0:3000`) and P2-db (`0.0.0.0:5442`) listeners disappear from the LAN/tailnet. |
| What does *not* stop | Mac sshd (22), SMB (445), router WAN SSH — separate items (§6, §7). |
| Auto-return | Docker Desktop is a login item and P3 uses `restart=always`; it comes back at next login/reboot unless the login item is disabled (an owner decision, not done). |
| Known dependency | P1's tunnel container cannot start (its config path is an empty dir, §5). The P1 public route is therefore already down; quitting changes nothing there. |
| Active-workload check | The dependency check that must be repeated *at approval time*: `docker ps` (running set), `docker stats --no-stream` (activity), per-project owner confirmation that no job/backup/trade is mid-run. P2/P5/P6/P8 are trading-related: confirm no position/session depends on them. |
| Rollback | Re-open Docker Desktop; restart policies restore P3; others start with `docker start <name>` (list in `~/asta-private/r6/containers.json`). Volumes are untouched by quitting. 7 stopped + 2 running PG volumes are restore-verified and 10 images archived (R5). |
| If Docker stays | Containment without quitting: bind P3/P2-db to loopback only by recreating those containers (needs owner approval per project), or enable the macOS application firewall + stealth (GUI, no sudo needed by the owner at the console), or restrict by the router's LAN rules. None applied. |

**Exact approval required (owner, in writing, one line each):**
1. "I approve quitting Docker Desktop on this Mac at <time>; I confirm no active workload of P1–P8 depends on it."
2. Separately and optional: "I approve disabling the Docker Desktop login item."
Without (1) nothing is stopped.

## 2. P1–P8 preservation register

Alias detail (real names, container names) is in `~/asta-private/r6/containers.json` and the alias map.

| Id | Kind | Containers / volumes | Source on this Mac | Backup / restore evidence | Secret/env inventory (names only) | Destination (proposed) | Reconstructable |
|---|---|---|---|---|---|---|---|
| P1 | finance app + tunnel | 4 containers; DB volume | **none** (R4 claim was wrong; dirs are Docker-created empty) | DB volume restore-verified (R5) | DB credentials, tunnel token, app secret | separate host/VM after owner supplies the repo | NO without owner's source |
| P2 | trading control | DB container (5442) | none | DB volume restore-verified | DB password | separate host | data yes; app needs source |
| P3 | chat UI | 1 container | image only | image archived | admin/API keys in volume | separate host | yes (public image + volume) |
| P4 | production stack | 7 containers | none | 7 PG volumes restore-verified in aggregate | many | separate host | data yes, app needs source |
| P5 | trading OS + dev pack | containers | none | images archived | exchange/DB keys (never read) | separate host | needs source |
| P6 | trading agent audit DB | DB | none | restore-verified | DB password | separate host | data yes |
| P7 | workspace | compose project | standalone repo exists (`~/my-workspace`) | repo + volume | DB password | stays or moves | YES |
| P8 | trading agent | containers | none | images archived | exchange keys (never read) | separate host | needs source |

Migration sequence (per project, only after owner approval): (1) freeze writes; (2) `docker save` image + `docker run --rm -v`
tar of the volume to the external SSD; (3) checksum; (4) restore into a scratch Docker on the destination; (5) start with
`--network none` first, validate row counts (the R5 verifier does this for PG); (6) switch over; (7) keep the source stopped
(not removed) for 14 days. Rollback = start the stopped source. **No deletion is part of this register.**
P1 tunnel: **not restored** — no authentic config exists and its exposure (public hostname → local web/api) has not been reviewed.

## 3. Public / private repository exposure assessment

- Remote is public, 0 forks, 0 stars, head 924da22. The public range contains only the already-public synthetic test
  token (G-14). Real project names and the LAN prefix exist only in **unpushed** local commits. The WAN IP and tailnet
  name were never written to the repository.
- Classification: internal project identifiers = *internal metadata, low sensitivity* (not credentials; reveal what the
  owner runs); network addresses (LAN prefix) = *internal metadata*, WAN IP/tailnet name = *sensitive, never committed*;
  gitleaks finding = *synthetic, not a secret*.
- Therefore nothing sensitive has been published; the risk is future publication of the unpushed range.

## 4. Git hygiene and history-remediation proposal (no rewrite, no push)

Preferred over a sixth gitleaks exception. Prepared and validated in a **scratch clone**, real repo untouched:
- Archive of the originals: `~/asta-private/r6/asta-original-9e1f552.bundle` (+ sha256, 0600).
- Replacement map `~/asta-private/r6/replace-map.tsv` (outside the repo). `scripts/history_scrub.py` (tree-filter,
  limited to `924da22..HEAD`, only touches files that differ from BASE, so already-public content is unchanged).
- `scripts/privacy_scan.py --repo --map [--range]` prints counts/short hashes only; result on the scratch result: clean.
- gitleaks on the sanitized branch: clean (the 3b2a631 finding disappears with the rewritten commit). Full tests pass there.
- The result is a fast-forward of 924da22 (public history is not rewritten), so a later push needs **no force**.

Owner-approved adoption procedure (not run): verify bundle sha256 → `git fetch <scratch> sanitized` → compare trees →
`git branch backup/pre-sanitize` → `git reset --hard sanitized` → rerun scans → push only on separate written approval.
Note the commits made after the proposal (this R6 round) need the same filter pass; re-run the scratch procedure at adoption.

## 5. Cleanup candidates and safety verification (nothing removed)

`docs/evidence/m3a/r6-cleanup-candidates.json` (alias paths).
- 5 directories (container dir → P1 dir → infrastructure → cloudflared → `config.yml/`): all created 2026-11-14 18:11:48 by
  Docker's file-sharing when P1's tunnel container restarted after my R2 Docker start; 0 files below; the leaf is a
  directory where a file was expected (the cause of the tunnel's exit 127). Dependency: the P1 tunnel container's
  bind mount. Removal is safe for data but Docker would recreate it if that container is started — remove the container's
  bind only by owner decision. Exact removal (after separate authorization): `rmdir` leaf→root in that order (rmdir refuses non-empty).
  Rollback: `mkdir -p` the same path (contents were empty).
- 7 scratch volumes by exact immutable IDs (listed in the private alias map): anonymous, created 14:09:14–14:09:45Z during my R5
  verification, referenced by no container (`docker ps -a --filter volume=<id>` empty at the time of the check). Exact removal
  after authorization: `docker volume rm <id>` per ID, one by one; the safety classifier denied it in R5, so no workaround was attempted. Rollback: none needed
  (contents were throw-away restore copies; originals untouched and re-creatable by `scripts/docker_pg_volume_verify.py`).

## 6. Router hardening recommendations (not applied)

RouterOS answers SSH on the WAN side (priority external issue), Telnet/HTTP on LAN. Plan (owner, from the console, safe mode):
1. `/system backup save` + `/export` first; 2. Safe Mode on; 3. disable telnet/www/ftp/api on all interfaces;
4. restrict `/ip service ssh` address list to the management host only, or disable ssh; add input-chain drop for
   `in-interface-list=WAN` except established/related; 5. disable MAC-server/neighbor discovery on WAN; 6. set strong admin credential and update
   RouterOS; 7. verify from outside (`nc -vz <WAN> 22` refused). Rollback: leave Safe Mode without confirming, or import the saved export.
Management dependencies to confirm before step 4: who manages it remotely, and from which address.

## 7. Mac dedication readiness

Not ready. Findings: sshd (22) and SMB (445) listening on LAN; application firewall off; auto-login on; FileVault off; Docker
Desktop login item. Recommendation (owner at console, no sudo by me): turn on FileVault and the firewall (stealth), disable auto-login,
turn off File Sharing and Remote Login unless needed (Remote Login with key-only if kept), disable the Docker login item after §1 approval.
Storage: 59.3 GB unallocated; Docker.raw 50.6 GiB must leave this host (after §2) before the two VMs + audit store fit with margin.
Security-host provisioning + rollback: unchanged runbook (`HOST-HARDENING-RUNBOOK.md`); the VMs stay unprovisioned.

## 8. Network prerequisites (corrected design)

Real resolver = DHCP-provided public 8.8.8.8 (router serves no DNS/NTP); no global IPv6; tailnet peers 0. The firewall VM rules now
use explicit `UPSTREAM_DNS` and `NTP_SERVERS` defines (public addresses) instead of the router as resolver. `render-rules.sh`
(`--detect` to display facts, `--dns A,B --ntp A,B --out FILE`) substitutes them and **refuses** private/loopback/link-local/multicast/CGNAT/
placeholder/malformed/empty values (exit 1, `REFUSING:`); covered by tests; the netns lab (38/38) is updated. Router is not modified.

## 9. Alerts coverage (G-13)

Off-host alerts must cover application and bridge failures, not only the firewall VM: the dead-man heartbeat has to carry an
application/bridge-liveness stamp (app→fw status, signed by the already-isolated link) so that a hung app or dead bridge pages the
owner. Specified here as a prerequisite for credential readiness; **not yet implemented** (remains open).

## 10. Findings (High/Critical) — unchanged and open

F-01, F-02, F-03, H-1, H-3 open. R-2 corrected: hosted CI *did* run (29 runs, 26 ok, 3 failed); remote head 924da22 was red from 10 mypy
errors in tests, fixed locally in c4b40dd. G-14 (history finding) → resolved by the proposal once adopted. New: G-15 router WAN SSH (external, owner),
G-16 Mac not dedicated (§7). N-R2 (resolver) resolved by design, pending deployment.

## 11. Safeguards preserved

Live-lock tests and the default-deny `ExecutionGateway` are byte-identical to `fb7770b`; M0–M2 frozen evidence verifies; no credentials, signing,
external orders or live trading anywhere.

## 12. M3-B decision

**NO-GO** for credential readiness and for acceptance. No acceptance tag. Preconditions: owner decisions §1/§4/§5/§6/§7, F-01/F-02/F-03/H-1/H-3 closed,
G-13 implemented, security host provisioned.
