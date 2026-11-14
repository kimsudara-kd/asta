# M3-B final gate matrix

Result: PASS / FAIL / OPEN (not satisfied) / BLOCKED (cannot be evaluated: external dependency) / N-A. Closure: CLOSED only when PASS with evidence at final HEAD.
Owner abbreviations: ENG = ASTA engineering, OWN = platform owner (console/privileged), REV = independent reviewer, CEO.

## Engineering gates
| Gate | Requirement | Result | Evidence | Owner | Closure |
|---|---|---|---|---|---|
| E-1 | No gateway bypass | PASS (offline) | `tests/orders/test_gateway_boundary.py`, `tests/live_lock/*`; ACCEPTANCE §10 | ENG | CLOSED offline; REV confirms |
| E-2 | No duplicate financial posting | PASS | paper integrity `--verify`; `tests/orders` fill tests | ENG | CLOSED |
| E-3 | No silent fill conflicts | PASS | `order_fill_conflicts`, `tests/integration/test_m3b_integrity.py` | ENG | CLOSED |
| E-4 | No unauthorized intent acceptance | PASS | `tests/orders/test_channel_hardening.py` | ENG | CLOSED |
| E-5 | No unapproved kill-switch release | PASS | `tests/integration/test_risk_bridge.py`, 0011 `session_user` | ENG | CLOSED |
| E-6 | No unexplained ledger discrepancy | PASS | 900/900, 0 discrepancies, digests = frozen | ENG | CLOSED |
| E-7 | No active secret exposure | PASS | `secret_scan` 0; gitleaks candidate clean (real repo history: 1 synthetic) | ENG | CLOSED (candidate) |
| E-8 | No unverified mandatory regression | **BLOCKED** | 8 Docker skips, E2E port conflict, skip guard fails | ENG/T | OPEN |
| E-9 | All security-critical components supervised | PASS (units, offline) / OPEN (deployed) | `infra/security-host/mac/app-vm/*.service`, tests | ENG/OWN | OPEN until deployed |
| E-10 | Off-host alerts cover app, bridge, firewall, kill switch, reconciliation, feed, backup | PASS offline / OPEN live | `test_offhost_alerts.py`, SECURITY-EVENTS §5 | ENG/OWN | OPEN (drills AL1–AL9) |
| E-11 | Synthetic signing + testnet-only boundary | PASS offline | `tests/security/test_synthetic_signer.py` | ENG | CLOSED offline |
## Infrastructure gates
| Gate | Requirement | Result | Evidence | Owner | Closure |
|---|---|---|---|---|---|
| I-1 | Dedicated/isolated host | FAIL (shared) | `host-posture-2026-11-14.json` | OWN | OPEN |
| I-2 | Independently enforced egress boundary deployed | OPEN | lab only | OWN/REV | OPEN (F-02) |
| I-3 | Verified host hardening | FAIL (measured: 5 gaps) | `host_posture.py`, `HOST-HARDENING-CHANGESET.md` | OWN | OPEN |
| I-4 | Verified backups and recovery | PARTIAL | 9 PG volumes restore-verified; non-DB volumes unverified; ceremony prepared | ENG/OWN | OPEN |
| I-5 | Adequate storage and memory | OPEN | 54 GiB free; needs Docker image removal (package ready) | OWN/CEO | OPEN |
| I-6 | Verified reboot behavior | BLOCKED | needs approved window + provisioned VMs | OWN | OPEN |
| I-7 | Off-host critical-service monitoring live | OPEN | no external destination configured | OWN | OPEN |
| I-8 | Docker storage recovery | BLOCKED | `DOCKER-STORAGE-RECOVERY-PACKAGE.md`; daemon fault G-17 | CEO/OWN | OPEN |
## Governance gates
| Gate | Requirement | Result | Evidence | Owner | Closure |
|---|---|---|---|---|---|
| G-1 | Sanitized repository candidate verified | PASS (candidate) | ACCEPTANCE §15, `PUBLISH-PLAN.md` | ENG | CLOSED for candidate; adoption OPEN |
| G-2 | Hosted CI executed | OPEN (unverified) | `ci.yml` parses; never run | CEO (push approval) | OPEN |
| G-3 | Independent security review completed | **BLOCKED — REVIEWER NOT APPOINTED** | review package | CEO/REV | OPEN |
| G-4 | All High/Critical findings closed | FAIL | F-01, F-02, F-03, H-1, H-3 open | all | OPEN |
| G-5 | Explicit CEO acceptance decision | OPEN | — | CEO | OPEN |
| G-6 | Separate credential and authenticated-demo authorization | OPEN (not given) | `BINANCE-TESTNET-ACTIVATION-PLAN.md` | CEO | OPEN |
