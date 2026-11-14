# M3-B-FINAL execution register (Phase 0)

Baseline established 2026-11-14 from the live host, not from earlier reports: repo `~/asta`, branch `main`, HEAD at start `8beb277`, clean tree, 19 unpushed commits over remote head `924da22`
(remote re-read with `git ls-remote`). `docs/evidence/M3B-R6.md` present and read; frozen M0–M2 evidence verifies; dev PostgreSQL at 5640 was **stopped by me** after the earlier regression
(root cause of the paper-integrity error, see Phase 1); two paper sessions in the dev DB; ASTA dev API/web running on loopback 4640/3640; Docker Desktop up with 5 containers (P1 ×3, P2-db, P3);
`Docker.raw` 51 GB allocated; external SSD mounted with the R5 archive (checksum OK). Rollback baseline: `~/asta-private/r6/asta-original-9e1f552.bundle` (+sha256) and git history (local commits only).
Blocker classes: **E** engineering (mine to finish), **P** privileged host action (reserved), **D** destructive/interrupting (reserved), **X** external party (reviewer, owner, exchange), **T** tooling/environment fault.

| ID | Work item | Existing | Missing at start | Depends on | Owner | Verification | Status | Class |
|---|---|---|---|---|---|---|---|---|
| P1 | Paper integrity failure | `scripts/paper_integrity.py` | DB down → traceback; no frozen comparison | dev PG | ASTA eng | `--verify` PASS, 3 tests | **DONE** | E |
| P2a | Docker preservation verify | R5 archive, PG restore proofs | non-DB volume archives; image-level backup | Docker daemon | ASTA eng | checksum, tar listing | PARTIAL: image tar OK; volume tar blocked (daemon cannot create containers) | T/D |
| P2b | Docker image removal | — | removal package | owner approvals | CEO/owner | `DOCKER-STORAGE-RECOVERY-PACKAGE.md` | PACKAGE READY, not executed | D |
| P3 | Host hardening | runbook | change set + measured posture | sudo | owner at console | `scripts/host_posture.py`, `HOST-HARDENING-CHANGESET.md` | AUDIT DONE, apply BLOCKED | P |
| P4 | ARM64 VM deployment | scripts, rules, lab 38/38, rehearsals | provisioning (sudo, vmnet), host reboot test | sudo, disk | owner + eng | netns lab, qemu pin, dry-runs | BLOCKED (design/tests only) | P |
| P5a | Gateway/financial/intent/kill-switch | M3-B R2–R5 | — | — | ASTA eng | order suites in full regression | DONE (offline) | E |
| P5b | Off-host alerts incl. G-13 | dead-man, monitor | app/bridge liveness, reconciliation/feed/backup alerts, notifier | — | ASTA eng | `tests/integration/test_offhost_alerts.py` (4) | **DONE offline**; live drills AL1–AL9 need VMs + external service | E/P |
| P6 | Vault + Binance testnet readiness | vault, feasibility | synthetic signer + mock endpoint; activation plan; ceremony | — | ASTA eng | `tests/security/test_synthetic_signer.py` (18) | **DONE offline**; real keys not authorized | E/X |
| P7 | Repo privacy + CI | scratch candidate, CI file | rebuild with all work; author identity; verification | — | ASTA eng | gitleaks, privacy, secret scans | CANDIDATE VERIFIED; publish needs approval | E/D |
| P8 | Full verification | suites | consolidated record | Docker for lab tests | ASTA eng | acceptance §14 | DONE except Docker-dependent tests (BLOCKED) | E/T |
| P9 | Independent review | package | reviewer | appointment | CEO | — | **BLOCKED — REVIEWER NOT APPOINTED** | X |
