# M3-A acceptance evidence (ASTA-M3A-IMPLEMENTATION-01)

**Final status: `ACCEPTANCE CANDIDATE` for M3-A engineering scope, with credential readiness explicitly NOT granted.** Credential/authenticated-demo readiness is **BLOCKED** by three open High findings (independent security review outstanding, legacy M2 host processes outside the boundary, proxy egress not independently firewalled). M3-A is **not accepted** (needs CEO review), **no M3-A acceptance tag was created**, **M3-B was not started**, nothing was pushed (no remote exists).

Baseline: tag `m2-accepted-with-residual` = `fe8194c`. Work commits (local): `23cc022` (egress boundary, vault/binding contracts, capability probes), `4d8691f` (documents/tests), `1cdb07a` (removed a secret-shaped test literal; narrow gitleaks fingerprint), plus the final documentation commit that contains this file (`git log -1 -- docs/evidence/M3A-acceptance.md`).

## 1. Scope delivered vs directive
| Stage / workstream | Result | Documents |
|---|---|---|
| M3A-0 read-only audit, plan, matrix | done before implementation; M2 supervisor and sessions untouched | `docs/m3a/SECURITY-ARCHITECTURE.md` |
| A / M3A-1 infrastructure egress | internal Docker network + hardened default-deny CONNECT proxy; 78 negative checks from inside the container | `docs/m3a/EGRESS-ENFORCEMENT.md`, `infra/egress/` |
| B / M3A-2 credential vault | mock-only vault contract, permissions, masking, audit, encryption at rest, rotation/revocation | `docs/m3a/CREDENTIAL-ISOLATION.md`, `src/asta/security/` |
| C environment isolation | immutable demo bindings, URL-override/redirect/class-mismatch refusal, signed 2-reviewer privilege config | same |
| D / M3A-3 capability matrix | Binance Spot Testnet (public endpoints RUNTIME VERIFIED), OKX (BLOCKED + DOCUMENTED excerpts) | `docs/m3a/EXCHANGE-CAPABILITY-MATRIX.md` |
| E / M3A-4 security review | internal threat model + findings register + review package; **independent review outstanding** | `THREAT-MODEL.md`, `INDEPENDENT-REVIEW-PACKAGE.md` |
| F paper diagnostics | read-only root-cause report; no strategy/limit/capital change | `PAPER-REJECTION-ANALYSIS.md` |
| M3A-5 final verification | below | this file, `M3B-READINESS.md` |

## 2. Verification commands and results (final tree)
| Check | Command | Result |
|---|---|---|
| Full Python suite | `ASTA_RUN_NETWORK_TESTS=1 python -m pytest -q` (`docs/evidence/m3a/pytest-full.log`) | **542 passed, 1 skipped, 0 failed** (465 M0-M2 + 77 new M3-A: 37 egress, 40 security). The skip is the OKX live probe (DNS sinkhole) - not a pass |
| Egress negative tests inside the container | `docker exec asta-protected-probe python /app/negative_tests.py` | **78 checks, 0 failed**: run 1 (1 failure from my over-broad mount heuristic, fixed and disclosed), run 2 after `docker restart`, run 3 after profile restore, run 4 final (`negative-tests-run*.json`) |
| Also asserted by pytest | `tests/m3a/test_egress.py::test_negative_tests_pass_from_inside_the_protected_container` | passed (would skip, not pass, if the boundary were down) |
| Browser acceptance | `node e2e/run.mjs` (`e2e-full-run.log`) | live 16, nodata 1, aging 1, research 12, paper 9 = **39/39** (the suite overwrites `docs/evidence/m2/screenshots`; I restored the accepted M2 files with `git checkout`, verified diff empty) |
| Ruff / format | `ruff check . ; ruff format --check .` | clean (whole repo incl. `infra`) |
| mypy strict (container) | README command | **no issues, 150 files** |
| Pyright | `pnpm dlx pyright --pythonpath .venv/Scripts/python.exe src` | `src`: **0 errors** (the 29 test/script findings from M2 remain, noncritical F-15) |
| Dependency audits | `python -m pip_audit` ; `pnpm audit` | no known vulnerabilities / no known vulnerabilities |
| Secret scan | `python scripts/secret_scan.py` | 0 findings |
| gitleaks history | README command (53 commits) | **no leaks** after fixing a finding in my own test (a secret-shaped synthetic literal, commit `23cc022`); the historical string is covered by one narrow fingerprint entry in `.gitleaksignore`, no rule or path allowlist was added |
| gitleaks working tree (`dir`) | `gitleaks dir` (`gitleaks-dir.json`) | 5 hits in the **untracked, git-ignored** `data/manual/creds.json` (seeded app-user logins from M0 manual testing, not exchange credentials) - recorded as finding F-08 |
| Live-lock / M0-M2 safety | `tests/live_lock`, `tests/contracts` inside the full suite; `git diff fe8194c -- src/asta/risk src/asta/core` | pass; diff **empty** (gateway default DENY, no LIVE, no credential vocabulary; the live-lock tests were not edited) |
| Accepted evidence untouched | `git diff fe8194c --stat -- docs/evidence/{m0-r1,m1,m1-r1,m2} M0/M1/M2 records, M2-acceptance.md, docs/decisions` | **empty** |
| Paper sessions | `scripts/paper_integrity.py` (`paper-integrity-final.json`) | both RUNNING, same IDs and config hashes, replay verified (316 / 495 checkpoints), 0 reconciliation discrepancies, 0 unresolved critical incidents; Task Scheduler supervisor (lock PID 43108) kept running throughout, never interrupted |
| Other projects | Docker before/after (`docker-before.txt`) | only two ASTA containers and two ASTA networks were added; no other project's container, network, firewall, DNS, proxy or task was read or changed |

## 3. Mandatory acceptance gates (directive section 10)
Gate-by-gate verdicts are in `docs/m3a/SECURITY-ARCHITECTURE.md` section 4. Summary: gates 2-13, 16, 17 **met**; gate 1 **met for the protected runtime, not for the legacy host processes** (F-01); gate 14 met (0 Critical findings; 3 High open); gate 15 **recorded as an explicit blocker** (independent review outstanding).

## 4. Key findings that need CEO attention
1. **No credential readiness.** F-01 (legacy processes outside boundary), F-02 (proxy NAT egress not independently firewalled, VM/firewall recommended), F-03 (no independent review) are open High findings (THREAT-MODEL.md section 3).
2. **OKX remains BLOCKED**: `www.okx.com`/`ws.okx.com` resolve to 127.0.0.1 from this PC; the proxy refuses them (`resolves-to-non-public`); nothing was bypassed; OKX capability facts are second-hand search excerpts and must be re-read live (even the documentation tool could not reach okx.com or developers.binance.com).
3. **Binance Spot Testnet public endpoints work through the boundary** (REST and WebSocket upgrade). Observed differences from production: lower order rate limits (50/10 s, 160,000/day vs 100/10 s, 200,000/day) and a different order book.
4. **Paper strategies re-emit rejected intents** (95 % of 239 rejections are repeats of the same signal; 189 cap + 50 cash refusals); expected safeguards, avoidable noise; must be solved (idempotent IDs, de-duplication) before any demo order. Strategies were not changed.
5. A plaintext dev-login file `data/manual/creds.json` (git-ignored) exists in the work tree (F-08); I did not delete it.

## 5. Residual risks and unverified items
Everything in the findings register F-01..F-15; plus unchanged M2 residuals (R-1 OKX, R-2 hosted CI, R-3 now implemented for a protected runtime only, R-11 assumptions, 24/7 reliability). UNVERIFIED: Docker/Windows reboot persistence, container-escape resistance, websockets redirect behavior, OKX demo behaviour (header, `50101`), real exchange demo key permissions, vault backend, reviewer key custody.

## 6. Restrictions confirmed
No real or demo exchange credential was created, requested, stored or used; no authenticated request or order (demo or real) was made; no live trading, margin, futures, leverage, borrowing, withdrawal or transfer capability exists; no strategy, risk-policy, fee, exposure or capital change; no alteration of accepted M0/M1/M2 evidence; no GitHub push or new remote; no M3-B work; no M3-A acceptance tag; no security-policy weakening (live-lock tests and ruff configuration only gained targeted entries for the new infra/test files).

## 7. Operating the boundary
`docker compose -f infra/egress/docker-compose.egress.yml up -d` (start/restore runtime profile), `down` to remove (named volumes: none). Allowlist profile switching is a reviewed, versioned change (`EGRESS-ENFORCEMENT.md` section 5).
