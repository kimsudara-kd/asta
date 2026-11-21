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

## Sanitized candidate rebuilt on the final HEAD (2bd9e89) — prepared, NOT pushed
Scratch clone `/tmp/a/san2`, branch `sanitized`, head b8ca10f (28 rewritten commits + regenerated review manifest). Results: fast-forward of
the remote head 924da22 (still the remote head); gitleaks 128 commits, no leaks; privacy scan over 924da22..HEAD clean; secret scan 0;
manifest verified; ruff clean; mypy 123 files clean; full suite **1365 passed, 7 skipped**; all author/committer emails normalised to the
already-public identity (owner to confirm). The push (`git push origin sanitized:main`, fast-forward, no force) remains reserved action 10 and
needs your explicit written approval. Docker image removal remains reserved action 2 and was not started.

## Browser E2E closed (2026-11-21)
The earlier port conflict was ASTA's own dev API/web (4640/3640); I stopped those two processes (not P1–P8) and ran `apps/web/e2e/run.mjs`
with `E2E_PSQL` (local PG 5640). The first `live` failures were environmental: the python.org macOS Python has no CA bundle, so the Binance
WebSocket failed TLS verification (`CERTIFICATE_VERIFY_FAILED`); with `SSL_CERT_FILE=<certifi bundle>` the feed connects. Result:
**PHASE RESULTS live/nodata/aging/research/paper = all true** (16+1+1+12+9 tests passed). Note: the Next.js 16.4.3 build prints a one-time
"known security vulnerability" reminder and exits 1 on first attempt per cycle; a retry continues. `pnpm audit` was clean earlier — re-check the
Next advisory (registry advisories/bulk) before acceptance; this is an open follow-up, no upgrade was performed.

## Next.js security upgrade (2026-11-21)
`pnpm audit --prod` reported 7 moderate advisories in Next 16.4.3 (all fixed in >=16.4.5). Upgraded the single dependency to 16.4.5
(`pnpm add next@16.4.5`; lockfile only). Afterwards: audit "No known vulnerabilities", `tsc` clean, web unit tests 0 fail, browser E2E 5/5 phases.
The sanitized candidate predates this commit and must be rebuilt again at publish time (PUBLISH-PLAN step 0).
