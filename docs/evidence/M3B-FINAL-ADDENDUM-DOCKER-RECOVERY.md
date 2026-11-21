# M3B-FINAL addendum — Docker daemon recovered; boundary tests re-run

Date 2026-11-21. Local HEAD before this addendum: b640172. Status unchanged: **BUILT — NOT ACCEPTED, credential readiness NO-GO.**

- The Docker daemon created containers again without any action by ASTA (G-17 no longer reproduces; root cause still unknown, so it is
  *observed recovered*, not *fixed*). No restart, no sudo, no P1–P8 workload touched.
- Full suite with real Docker: **1365 passed, 7 skipped** (4 opt-in network, 1 PowerShell, 2 boundary skips that were resolved below).
- ASTA's own egress boundary (compose project `asta-egress`) was brought up, tested and torn down:
  `tests/m3a/test_egress.py` **39 passed, 0 skipped** (70+ negative tests from inside the protected container, kernel rules and the
  compromised-proxy simulator). Containers and the two ASTA networks were removed afterwards; no other project's resources were used.
- Still BLOCKED/unchanged: browser E2E (API port 4640 in use), reviewer not appointed, hosted CI unverified, host hardening, VM
  deployment, Docker storage recovery, credentials. F-01/F-02/F-03/H-1/H-3 remain open (the container boundary is not a dedicated VM).
