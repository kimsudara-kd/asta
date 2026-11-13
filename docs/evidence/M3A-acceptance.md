# M3-A acceptance evidence (ASTA-M3A-IMPLEMENTATION-01)

> **Superseded in status by the M3A-R1 section at the end of this file (R1: BUILT — NOT ACCEPTED, credential readiness NO-GO).**

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


---

# M3A-R1 — Security boundary remediation (evidence update)

**R1 final status: `BUILT — NOT ACCEPTED`** (the three High findings F-01, F-02, F-03 are not closed). **Credential readiness: NO-GO.** M3-B not authorized or started; no M3-A acceptance tag; no push; no credential, signed request, authenticated call or order of any kind; ExecutionGateway default DENY untouched; M2 supervisor and both paper sessions never interrupted. Baseline `445c578`; R1 commits (local): `c8d9fe3` (gate), `968252f` (documents, tests, evidence), plus the following hygiene fix and the final documentation commit (`git log --oneline 445c578..HEAD`).

## R1-1. Deployed security architecture and verified enforcement (Workstream A)
* **Audit of processes outside the boundary:** `docs/evidence/m3a/r1-process-audit.txt` - the Windows-host supervisor and `paper_service` (the latter connects directly to Binance public hosts) are outside; they hold no credentials. They stay unchanged until a separately authorized, verified migration.
* **Dedicated VM: not provisionable on this PC** (Windows 11 Home, no administrator rights, no VirtualBox/VMware/QEMU/Hyper-V; WSL2 distributions share the Docker VM and other projects). **F-02 stays OPEN.** Architecture, CPU/RAM/disk/OS/network requirements and deployment/rollback plan: `docs/m3a/VM-BOUNDARY-DESIGN.md`.
* **Built and verified instead (not a substitute for the VM):** a kernel-enforced gate. The proxy now runs inside the network namespace of `asta-egress-gate`, which installs nftables default-DROP in input/output/forward (IPv6 included), allows only TCP to gate-resolved public IPv4 addresses of the allowlisted names on 443/9443, serves DNS only for those names (REFUSED otherwise) and accepts inbound 3128 only from the protected subnet. The proxy (uid 65534, no capabilities, read-only fs) cannot alter these rules. Config: `infra/egress/` (version-controlled; subnets pinned 172.31.250.0/24 and 172.31.251.0/24, no host ports).
* **Actual network-enforcement verification** (`python infra/egress/verify_boundary.py` -> `r1-boundary-verification.json`; final runs `r1-negative-tests-final.json`, `r1-gate-tests-final.json`): protected-runtime negative checks **78/78** and compromised-proxy checks **39/39** (unprivileged attacker process inside the proxy's namespace: direct IPs/ports, UDP DNS to public resolvers, tunnelling-style names, IPv6, metadata, host/LAN/docker-host, self-weakening attempts) in four phases - baseline, after proxy restart, after gate restart, after recovery; kernel drop counters confirm the kernel did the dropping. **Gate stopped -> the protected runtime has zero connectivity (fail closed)**; recovery restores service and a positive control (`CONNECT data-api.binance.vision:443` -> 200, TLS `GET /api/v3/time` -> 200) passes.
* **VM/firewall restart tests:** service, container and gate restarts verified; **Docker Desktop/VM restart and Windows reboot NOT performed** (would interrupt every other project and the M2 database) - UNVERIFIED (F-14). No host firewall, network adapter, DNS or other project's configuration was changed (Docker before/after diff: only ASTA containers and networks).

## R1-2. Independent security review (Workstream B)
**OPEN - no reviewer designated, no review performed.** The package is complete: trust-boundary diagram, threat model, egress policy and negative-test results, authentication/authorization controls, mock vault isolation, credential lifecycle, environment-separation design, vulnerabilities, remaining attack paths, M0-M2 regression evidence (`docs/m3a/INDEPENDENT-REVIEW-PACKAGE.md`, `TRUST-BOUNDARIES-AND-LIFECYCLE.md`, `THREAT-MODEL.md`). The developer's own testing is not presented as independent.

## R1-3. Credential hygiene report and gitleaks exception review (Workstream C)
`docs/m3a/CREDENTIAL-HYGIENE.md`. `data/manual/creds.json` (3 app-user logins from an M0 manual e2e run; key names only inspected): **obsolete** (users exist in no database; none in the observation DB, 0 in the test DB), no workflow dependency, **0 leaks** of its values in the work tree and the full git history. Not exchange credentials. **Removal not performed: the sandbox policy denied the delete command and I did not work around it** - owner action `Remove-Item E:\Dev\asta\data\manual\creds.json`. A hash-pinned test fails on any new credential-like file. Gitleaks exception review: two single-fingerprint entries (synthetic scrubber-test string; a SHA-256 integrity pin), each verified to suppress exactly one finding (exception-free clone scan: 1 finding), justified, no rule/path allowlist; history rewrite rejected (would change accepted baseline hashes).

## R1-4. Evidence protection (Workstream D)
* Found two real defects: (a) every browser run overwrote the frozen M2 screenshots; (b) the historical `*.log` evidence of M0, M1 and M2 was git-ignored and untracked (not in the tags).
* Fixed: e2e screenshots are written per milestone and run under `data/e2e-shots/<milestone>/run-<timestamp>/` (default milestone `m3a`; `E2E_PUBLISH=1` copies to `docs/evidence/<milestone>/screenshots/<run>`); runs for `m0-r1`, `m1`, `m1-r1`, `m2` are refused and `helpers.ts` throws if pointed at them; the historical logs are now tracked (`.gitignore` exception `!docs/evidence/**/*.log`, content unchanged); manifest `docs/evidence/FROZEN-EVIDENCE.json` pins the three acceptance tags' commits and SHA-256 of every frozen file (plus the previously untracked logs); `scripts/verify_frozen_evidence.py` verifies working tree and HEAD against the manifest and the tags; 3 regression tests (including tamper, addition, removal and tag-move detection on a scratch repository). Result: **frozen evidence intact**, and the full browser run no longer modifies any tracked file (git status clean after the run).

## R1-5. Exchange feasibility (Workstream E)
Reconfirmed through the gated boundary (`r1-capability-probe.json`, appendix in `EXCHANGE-CAPABILITY-MATRIX.md`): Binance Spot Testnet public REST (ping/time/exchangeInfo/depth/klines) and WebSocket upgrade **RUNTIME VERIFIED**; testnet ORDERS limits 50/10 s and 160,000/day vs production data host 100/10 s and 200,000/day. OKX **BLOCKED** (gate refuses the sinkholed address; documentation fetch also refused) - unresolved limitation preserved, not bypassed. Authenticated capabilities remain DOCUMENTED/UNVERIFIED; no fixture exists for any private endpoint by design.

## R1-6. Paper rejection diagnostics (Workstream F)
Read-only addendum in `docs/m3a/PAPER-REJECTION-ANALYSIS.md`: 264 rejected BUY signals (189 cap, 75 cash) are 12 distinct intents; `signal_id` is a per-account counter (118 distinct numbers) and cannot de-duplicate; 253 of 264 repeat on the next bar with the same reason; identical strategy-state evaluations explained; five de-duplication boundaries quantified (D1 suppresses about 95 %, D2 about 91 %); proposals for a later approved milestone. No strategy, limit or session configuration was changed.

## R1-7. Complete regression (final tree)
| Check | Result |
|---|---|
| Full Python (`ASTA_RUN_NETWORK_TESTS=1`, `r1-pytest-full.log`) | **549 passed, 1 skipped (OKX live probe, DNS sinkhole - not a pass), 0 failed** (542 + 7 new: frozen-evidence 3, credential hygiene 2, gate/compose 2) |
| Browser (`r1-e2e-full-run.log`) | **39/39** (live 16, nodata 1, aging 1, research 12, paper 9); no tracked file modified |
| Ruff check + format | clean (whole repo) |
| mypy strict (container) | no issues, 152 files |
| Pyright | `src` 0 errors |
| pip-audit / pnpm audit | no known vulnerabilities / no known vulnerabilities |
| Secret scan / full-history gitleaks | 0 findings / no leaks (53+ commits, two reviewed single-fingerprint exceptions) |
| Infrastructure negative tests | 78/78 and 39/39 final runs; matrix in `r1-boundary-verification.json` |
| M0-M2 safety | live-lock and contract tests inside the full suite pass; `git diff fe8194c -- src/asta/risk src/asta/core` empty; frozen-evidence verifier green |
| Paper ledger reconciliation | both sessions RUNNING, same IDs/hashes, replay verified (321 / 549 checkpoints), 0 discrepancies, 0 unresolved critical incidents (`r1-paper-integrity.json`); supervisor lock PID 43108 unchanged |

## R1-8. Outstanding findings and recommendation
Open High: **F-01** (legacy host processes outside the boundary), **F-02** (no dedicated VM; mitigated by the kernel gate, not closed), **F-03** (no independent review). No Critical. Open Medium/Low: F-04..F-07, F-09..F-14, F-18 (see `THREAT-MODEL.md`); F-16 closed, F-17 accepted, F-08 pending owner deletion. **M3-B readiness: NO-GO** (`docs/m3a/M3B-READINESS.md`). Decisions needed from the CEO: designate the independent reviewer; choose the dedicated boundary technology and authorize the host changes; confirm deletion of `creds.json`; provide a lawful OKX path or accept Binance-testnet-only scope.
