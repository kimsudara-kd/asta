# Dedicating the Mac mini to ASTA: unrelated-project inventory and migration plan (ASTA-M3B-R4, workstream A)

**Inventory and plan only. Nothing unrelated was stopped, deleted, migrated or modified.** The only writes to anything outside this repository were temporary, then removed: database *dumps* taken from two running databases (read-only `pg_dump`) restored into a scratch database to prove restorability, and read-only mounts of two volumes. No migration may be executed without your separate approval.

## 0. Disclosure: a side effect of my earlier work (action needed from you)
In ASTA-M3B-R2 I started Docker Desktop (a user application) to run the Docker/firewall security gates. Docker's backend came up at 18:11 local time and the daemon **restarted every container whose restart policy is `unless-stopped`/`always`** (`docker inspect`: containers started 11:11:48Z, the same minute). Because of that, five unrelated containers have been running on this Mac since then, two of them publishing on **all network interfaces**: `P3` (`0.0.0.0:3000`) and `P2-db` (`0.0.0.0:5442`, PostgreSQL). `P1-tunnel`, which had been running since 2026-11-05 until the daemon went down, failed to restart (exit 127) and is **not running**, so any public tunnel it provided is currently down. I did not stop or restart anything, because the directive forbids touching unrelated projects. **Decision needed now (D-0):** (a) quit Docker Desktop (stops all of them; the previous state of "Docker not running" returns), or (b) leave them as they are, or (c) fix the P1 tunnel if that service matters. I recommend (a) or binding the two ports to `127.0.0.1` at the next planned change; the exposure is real on this LAN because the application firewall is off.

## 1. What is on the host (read-only inventory, 2026-11-14)
Owner of all listed projects: the same person (the Mac's only human account `apple`); dependencies identified from compose labels, mounts and image names. Secrets were never read or printed (environment variables were not dumped).
| Project (compose) | Containers (state) | Published ports | Data | Dependencies | Source on this Mac |
|---|---|---|---|---|---|
| **P1** | `P1-{web,api,db}-1` **running**; `-cloudflared-1` exited (127) | none on the host (3000/tcp internal) | volumes `P1_postgres_data` 71 MB, `_import_data`, `_attachment_data` (0 B); DB 13 MB, 57 tables, 3,788 rows | Cloudflare tunnel (config + credentials mounted from files); locally built images `P1-web` 1.6 GB, `-api` 353 MB | `~/My Development/P1` (present) |
| **P2** | `P2-db` **running** | **`0.0.0.0:5442` -> 5432** | volume `..._pgdata` 570 MB; DB 248 MB, 129 tables, **412,096 rows** | none other found; `postgres:16-alpine` | `~/P2` **missing** (compose directory gone) |
| **P3** | `P3` **running** (`restart=always`) | **`0.0.0.0:3000` -> 8080** | volume `P3` 1.1 GB (SQLite `webui.db`, 43 tables, integrity ok) | an LLM backend it connects to (not identified) | image `ghcr.io/P3/P3:main` (re-pullable) |
| P4 | 7 containers exited 6 weeks ago (gateway, commerce, portal, postgres, minio, tunnel) | none while stopped | volumes: postgres 48 MB, product data, caddy | Cloudflare tunnel; images `ghcr.io/kimsudara/sdr-ecosystem/*:production` (registry-hosted) | `~/P4-dir/deploy` **missing** |
| P5 (prod + developer pack) | 5 exited (frontend, backend, db, tunnel, db of dev pack) | none | pgdata 49 MB + 84 MB, uploads 0 B | tunnel; locally built images | `~/Downloads/P5-devpack` (Downloads: not readable without Full Disk Access) |
| P6 | `P6-db` exited | none | `P6_sdr_audit_data` 48 MB | | `~/P6` **missing** |
| P7 (`app`) | frontend, backend, db exited | none | `app_db-data` 48 MB | locally built images | `~/Desktop/P7/app` (Desktop: not readable here) |
| P8 | api, dashboard, main-db, audit-db exited | none | `P8_{main,audit}_db_data` 49 + 48 MB | locally built images | `~/Documents/P8` (not readable here) |
Totals (`docker system df`): 27 images 15.1 GB (1.96 GB reclaimable), 24 containers, **88 volumes 7.06 GB** (5.0 GB reclaimable, mostly anonymous), build cache 4.4 GB; Docker's data disk `Docker.raw` occupies **50.6 GiB** on the internal SSD; Docker VM memory limit 7.75 GiB.
Other non-ASTA software on the host: `EXO.app` (distributed inference, listens on `*:49366` and `*:52415`), Tailscale (`tailscaled` as root; serve entries: `:443` -> 127.0.0.1:7040 which does **not** listen, `:8443` -> the ASTA dashboard), Microsoft OneDrive/Office/AutoUpdate agents, Google updaters, Claude desktop updater, Chrome, VS Code. System services reachable on the LAN address: **SSH (22), SMB (445), 88** (the application firewall is off). The memory notes of an earlier workspace mention other trading projects (forex bot, trading desk, workspace) that were **not found on this host**; they are not in this inventory.

## 2. Can the data be restored? (verified where possible)
| Source | Method | Result |
|---|---|---|
| `P1-db` | `pg_dump -Fc` inside the container -> `pg_restore` into a scratch PostgreSQL 16.2 database | **restored with no errors; 57/57 tables, 3,788/3,788 rows equal to the source** |
| `P2-db` | same (dump 20.5 MB) | **restored with no errors; 129/129 tables, 412,096/412,096 rows equal** |
| `P3` volume | read-only mount of the volume, SQLite `PRAGMA integrity_check` | **ok**, 43 tables (attachments/uploads inside the volume not copied) |
| 7 stopped PostgreSQL volumes | read-only mount: `PG_VERSION` present (16/17), 1,277-2,079 files each | **structure present; logical restore NOT verified** (needs a scratch server on a copy of the volume) |
| locally built images | none of them is in a registry | **not backed up**: `docker save` required before any removal |
The dumps and the scratch databases were deleted after the comparison (`pg_database` shows no `r4_*` left). Restorability of the stopped projects, of attachments/uploads and of the Cloudflare tunnel credentials must be proven by the owner's chosen method before anything is removed.

## 3. Migration or shutdown plan per project (prepared; not executed)
Common pre-steps for every project (once, owner-approved window): (1) record the compose definition (the missing source directories mean the definition would have to be reconstructed from `docker inspect`, which contains secrets: do this only into an encrypted location); (2) `docker save` every locally built image; (3) logical dump of each PostgreSQL database and `tar` of each non-database volume, onto the 3.6 TB external drive (it is exFAT: fine for these archives, not for VM disks); (4) verify by restoring into scratch (as done above); (5) only then stop, then remove.
| Project | Recommended end state | Steps specific to it |
|---|---|---|
| P1 | move to another machine or a hosted container platform; if it must stay on this Mac it contradicts the dedicated-host goal | decide whether the tunnel is production; export `postgres_data` + `attachment_data`/`import_data`; keep `~/My Development/P1` (the only source on disk) backed up before the move |
| P2 | dump (done in verification, repeat at migration time), run elsewhere | the compose directory is missing: recreate from `docker inspect`; **bind to 127.0.0.1** meanwhile |
| P3 | run on another machine, or keep only if the owner accepts it on the host and binds it to 127.0.0.1 | back up the 1.1 GB volume; check which LLM backend it uses |
| P4 / P5 / SDR agent / P7 / P8 | archive (images saved, DB dumps, volumes tarred) and **remove from this host**, or restart on another machine; they are stopped since 6 weeks (apparently dormant) | confirm with the owner whether each is still needed; the `ghcr.io/kimsudara/...` images can be re-pulled; local images cannot |
| EXO | uninstall or move; it listens on all interfaces | owner decision |
| Tailscale | keep (ASTA's phone access) or move behind the dedicated host policy | decide D-A1 |
| Microsoft/Google updaters, browsers | remove or accept as residual | residual of D-A |
Order of operations: back up and verify -> stop containers -> quit Docker Desktop and remove it from login items (or uninstall it: ASTA does not use Docker at runtime; the Docker boundary gates are a test tool) -> remove or relocate leftover data -> run the post-migration checks below. Rollback at every step = restore from the verified backups.

## 4. Final ASTA-only host state
macOS (current release, FileVault on, application firewall on with only required allowances, no auto-login, no file/screen sharing, Remote Login only if required and then key-only and restricted to the tailnet interface or a management VLAN), one human admin account plus the unprivileged `_astavm`; ASTA dev stack on loopback only (or retired once the VMs run); Tailscale optional; **no Docker, no unrelated containers or volumes, no EXO, no unrelated listeners**; VMs: `asta-fw` and `asta-app` (two-VM phase). Disk: Docker's 50.6 GiB freed.

## 5. Post-migration checks (read-only, prepared and executed once as a baseline)
`infra/security-host/mac/host-separation-check.sh [report.json]` (read-only, no sudo, tested): no unrelated running/stopped containers or volumes, no LAN-reachable listeners (22, 80, 88, 445, 3000, 5442, 49366, 52415 ... probed on the LAN address), ASTA ports loopback-only, no EXO, sharing services off, FileVault on, application firewall on, no auto-login, admin group size, unrelated launchd jobs listed for review. **Baseline today: 10 of 14 checks FAIL** (`docs/evidence/m3a/r4-host-separation-baseline-macos.json`) - the expected result for a shared host. The target is exit 0 after the approved migration. Not covered by the script (manual): Tailscale serve entries, login items, browser/updater agents, Time Machine/Spotlight indexing of VM disks (exclude `/var/lib/asta-vm`).
