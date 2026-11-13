# Independent security review: reading order and package index (M3A-R3)

Organises the review package (see `INDEPENDENT-REVIEW-PACKAGE.md`; file hashes in `docs/evidence/m3a/REVIEW-MANIFEST.json`, verify with `python scripts/review_manifest.py --verify`). Authored by the implementer; **independent review status: reviewer not designated, review not started (F-03 OPEN).**

## Suggested order for the reviewer
| Step | Read | Why |
|---|---|---|
| 1 | `docs/evidence/M3A-acceptance.md` (R3, R2, R1 sections first), `M3B-READINESS.md` | what is claimed, what is open, what is asked |
| 2 | `SECURITY-ARCHITECTURE.md`, `TRUST-BOUNDARIES-AND-LIFECYCLE.md` | architecture, zones, auth, credential lifecycle, remaining attack paths |
| 3 | `THREAT-MODEL.md` (threats T-01..T-19, findings F-01..F-18, status tables) | threats and the claimed controls |
| 4 | `EGRESS-ENFORCEMENT.md`, `VM-BOUNDARY-DESIGN.md` (R3, R2 sections), `HOST-INVENTORY-AND-FEASIBILITY.md` | boundary design, what is deployed (Docker) versus designed (dedicated host) |
| 5 | `infra/egress/` (proxy, gate, tests) and `infra/security-host/` (rules, units, provisioning, acceptance) | the artifacts themselves |
| 6 | `CREDENTIAL-ISOLATION.md`, `CREDENTIAL-HYGIENE.md`, `src/asta/security/`, `tests/m3a/test_security.py` | vault contract, environment binding, signed configuration, hygiene |
| 7 | `EXCHANGE-CAPABILITY-MATRIX.md`, `PAPER-REJECTION-ANALYSIS.md` | exchange feasibility and the duplicate-order risk |
| 8 | `docs/evidence/m3a/` raw outputs (negative tests, gate tests, boundary verification, probes, rehearsal, vault validation, regression logs), `docs/evidence/FROZEN-EVIDENCE.json` | reproducible evidence |

## Package map (approximate file counts at the R3 manifest)
| Area | Location | Contents |
|---|---|---|
| Documents | `docs/m3a/` | architecture, egress, credentials, threat model, matrix, readiness, VM design, inventory, review index/engagement, residual-risk register, diagnostics |
| Acceptance record | `docs/evidence/M3A-acceptance.md` | milestone evidence with R1/R2/R3 sections |
| Raw evidence | `docs/evidence/m3a/` | JSON/log outputs from every run, manifest, container posture, gitleaks reports |
| Boundary code/config | `infra/egress/` | gate (nftables+dnsmasq), proxy, compose, negative/compromised-proxy/probe scripts, restart verifier |
| Dedicated-host artifacts | `infra/security-host/` | nft rule sets, systemd units, netplan, provisioning, deploy/rollback, acceptance, vault validation |
| Contracts and tests | `src/asta/security/`, `tests/m3a/` | vault, binding, redaction; egress, security, evidence, hygiene, host-config, provisioning, legacy-isolation tests |
| Integrity tooling | `scripts/verify_frozen_evidence.py`, `scripts/review_manifest.py`, `scripts/m2_migration_rehearsal.py` | frozen evidence, review manifest, rehearsal |

## How to reproduce the headline results
1. `python scripts/review_manifest.py --verify` and `python scripts/verify_frozen_evidence.py` (artifact integrity).
2. `docker compose -f infra/egress/docker-compose.egress.yml --profile lab up -d`, then `docker exec asta-protected-probe python /app/negative_tests.py` (78 checks) and `docker exec asta-compromised-proxy-sim python /app/gate_tests.py` (39 checks); `python infra/egress/verify_boundary.py out.json` for the restart/fail-closed matrix.
3. `python -m pytest tests/m3a -q` (static and Docker-backed tests; Docker tests skip, not pass, if Docker is absent).
4. `python infra/security-host/acceptance/vault_validation.py out.json` (synthetic secrets; run on the Linux VM when it exists).
5. On dedicated infrastructure only: `build_seed.py`, a provisioning script, `deploy.sh`, then `vm_acceptance.py` pre/post around a real reboot.
