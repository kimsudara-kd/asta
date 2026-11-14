# Docker storage recovery — execution-ready package (M3-B-FINAL phase 2)

**Status: PREPARED, NOT EXECUTED.** The desired outcome (reclaim the Docker disk image) is blocked on reserved actions 1 and 2
(interrupting P1–P8 workloads; deleting the image). Project/host identifiers are aliases (key: `~/asta-private/r5/alias-map.md`).

## A. Verification performed 2026-11-14 (read-only)
| Item | Result |
|---|---|
| Image file | `~/Library/Containers/com.docker.docker/Data/vms/0/data/Docker.raw`; apparent 245 GB sparse, **51 GB actually allocated** (`du`); Mac free space 54 GiB of 228 GiB |
| Docker state | Desktop running; 5 containers up 4 h (P1 web/api/db, P2-db, P3); 24 containers total; 27 images (15.1 GB, 1.96 GB reclaimable); 95→82+ anonymous volumes + 16 named (7.4 GB, 5.3 GB reclaimable) |
| Listening | P3 `0.0.0.0:3000`, P2-db `0.0.0.0:5442` (reachable on LAN; refused from the Internet) |
| Image archive | `/Volumes/<ext>/asta-r5-docker-preservation/local-images.tar` 791,509,504 bytes; `shasum -a 256 -c` **OK**; tar readable (OCI layout). 10 locally built images. |
| DB backups | 9 PostgreSQL volumes restore-verified with exact row counts (R5, `scripts/docker_pg_volume_verify.py`) |
| External capacity | 3.1 TiB free on the external SSD: more than enough for a full copy of `Docker.raw` (51 GB allocated) |
| Active dependencies | Unknown without the owners: whether anybody uses P1/P2/P3 right now. Must be confirmed per project before downtime. |

### What is NOT proven (so a row-count check is not sufficient)
1. **No source repository** for P1, P2, P4, P5, P6, P8 exists on this Mac (R5). Images are archived only for locally built ones; P1's tunnel
   configuration is lost (empty directory). Recoverability of those applications therefore depends on the owner's own repositories.
2. Non-database named volumes (P3 1.1 GB, caddy, uploads, attachments) have **no verified archive** yet. A read-only tar of each volume
   via a throw-away container was attempted today and **could not run: Docker Desktop stopped creating containers** (every `docker run/create`
   hangs, existing containers keep running; see `docs/evidence/M3B-FINAL-ACCEPTANCE.md` §4). Nothing was restarted. The hung attempts were
   cleaned up by exact name (containers I created); the two anonymous volumes they created are scratch (IDs in the private alias map).
3. A complete **file-level backup of `Docker.raw`** requires Docker to be stopped (copying a live raw image is not consistent).

## B. Exact procedure (after the gates in §D)
Run by the operator at the Mac console. Replace `<EXT>` with the external SSD mount.
1. Announce downtime to owners of P1, P2, P3 (the only running workloads) and obtain each "no active use" confirmation.
2. Quit cleanly: Docker Desktop menu → Quit (or `osascript -e 'quit app "Docker"'`); verify: `pgrep -fl com.docker` prints nothing and `docker ps` reports the daemon unreachable.
3. Back up the image: `cp -c` is not valid across volumes, so `rsync -a --sparse --progress ~/Library/Containers/com.docker.docker/Data/vms/0/data/Docker.raw "<EXT>/asta-docker-final/Docker.raw"` then
   `shasum -a 256` both files; they must match; keep the checksum file next to the copy.
4. **Boot test the backup before deleting anything:** copy `Docker.raw` back is not needed; instead start Docker Desktop once, confirm the 5 containers and volumes appear (this proves the original is intact) and quit again. (Skip if the owner prefers; then restore is tested by the rollback below.)
5. Only after the owner's separate written authorization naming the exact path in §A: `mv Docker.raw <EXT>/asta-docker-final/Docker.raw.removed-from-mac` is the recommended "removal" (a move to external storage reclaims the same 51 GB and is reversible); permanent `rm` only after 14 days.
6. Verify reclaimed space: `df -h /` should show ≈ +50 GiB; Mac boots; sshd/SMB and other services unchanged.
7. Do **not** let Docker Desktop silently create a new empty image and call the old workloads "restored": either keep Docker Desktop disabled (`com.docker.helper` login item off), or start it knowingly as an empty engine.

## C. Rollback / recovery
* Before step 5: start Docker Desktop; everything is as before.
* After step 5: quit Docker; `rsync -a --sparse "<EXT>/asta-docker-final/Docker.raw" ~/Library/Containers/com.docker.docker/Data/vms/0/data/Docker.raw` (needs ~51 GB free), start Docker Desktop, verify containers, row counts (`scripts/docker_pg_volume_verify.py`), checksums.
* Independent of the image: `docker load -i local-images.tar`, volume restores from the logical dumps.

## D. Gates (all required, none satisfied today)
1. Written owner approvals for P1, P2, P3 downtime. 2. Written approval for step 5 naming the exact path. 3. Verified external copy of `Docker.raw` with matching checksum.
4. Source repositories for P1/P2/P4/P5/P6/P8 located or an explicit owner statement that they are reconstructible. 5. Non-DB volume archives verified once the Docker daemon can create containers again (or after step 3 by restoring the copy).
**Impact:** P1/P2/P3 down for ≈ 1–2 h (copy time); P2-db and P3 listeners disappear from the LAN for that period.
