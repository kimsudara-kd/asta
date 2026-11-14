# M3-A threat model (internal; independent review outstanding)

Authored by the implementing engineer. **It is not an independent review and must not be cited as one** (see INDEPENDENT-REVIEW-PACKAGE.md). Scope: ASTA before any authenticated exchange-demo connector exists, plus the controls a future connector must satisfy.

Assets: (A1) future demo credentials; (A2) the integrity of environment selection (demo vs production); (A3) the paper ledger and accepted evidence; (A4) the audit log; (A5) the host and other projects; (A6) operator accounts.
Trust boundaries: Internet <-> egress proxy; proxy <-> protected runtime; protected runtime <-> vault; host processes <-> Docker; operators/UI <-> API; AI/strategy modules <-> everything privileged.

## 1. Threat register (T-xx), current controls, attack tests

| ID | Threat | Controls now | Negative test / evidence | Residual (finding) |
|---|---|---|---|---|
| T-01 | Credential theft (memory, logs, files, repo, bundles, crash dumps) | no credential exists; `SecretValue`, redaction filter, scrubbed crash text, encrypted-at-rest backend, no `.env`, secret scan + gitleaks, frontend scan | `test_security.py` (mask/log/crash/audit/file), gitleaks history clean | real backend/key custody undefined; memory dump not addressed (F-05) |
| T-02 | Demo-to-production routing confusion | demo-only classes and bindings, immutable binding, no production binding, `LIVE` absent, signed config names demo only | binding tests (override, class mismatch, env refusal) | shared OKX host (F-04) |
| T-03 | Same-host demo/production (OKX) | no OKX credential; header recorded; docs | **cannot be enforced at network level** | F-04: M3-B must verify header + 50101 fatal handling with a real demo response; UNVERIFIED |
| T-04 | Unauthorized network access / arbitrary egress | internal network, default-deny proxy, app allowlist | negative tests N01-N13 inside the container | legacy host processes not inside (F-01); proxy on shared NAT (F-02) |
| T-05 | DNS rebinding / sinkhole / poisoned names | proxy resolves, pins IP, refuses any non-public answer, IPv4 only | resolver unit tests (mixed answers, rebinding to 127.0.0.1/169.254.169.254), live OKX refusal | malicious *public* answer for an allowed name accepted (F-09) |
| T-06 | SSRF (app asked to fetch internal/metadata URLs) | URL guard, IP-literal refusal, metadata/CGNAT/loopback ranges refused, internal net no route | N04, N07, N08 | none beyond F-01 |
| T-07 | Proxy bypass (direct sockets, alt proxies, SOCKS, tunnelling, env proxies) | no route out of the internal network; only CONNECT; targets validated | N06 | `trust_env` proxy env in legacy processes (F-11) |
| T-08 | Malicious redirects | app never follows redirects (3xx raises), binding rejects redirects, redirect targets still need the proxy policy | `test_public_http_never_follows...`, N10 | websockets library redirect behavior UNVERIFIED (F-10) |
| T-09 | Container escape / privileged abuse | non-root, cap_drop ALL, read-only, no-new-privileges, no socket/host mounts, no published ports, pid/mem limits | N12, compose test, posture file | kernel/Docker CVEs not tested (accepted, standard) |
| T-10 | Privilege escalation to host / Docker socket | no socket mounted; `privileged: false` | N07 | - |
| T-11 | Host filesystem exposure | no host mounts but two read-only single files | N12 | - |
| T-12 | Unauthorized trading API calls | no authenticated client, no signing code, gateway DENY, forbidden path fragments, live-lock tests | 15 live-lock tests, source scans | path guard is application-level only (host-level separation impossible) |
| T-13 | Compromised connector reads other credentials | vault identity separation, grants per connector+identity | tests (no grant, other identity) | not process-isolated (F-05) |
| T-14 | Duplicate or runaway orders | none yet (no order path); M2 evidence shows strategies re-emit identical rejected intents (95 %) | `PAPER-REJECTION-ANALYSIS.md` | Deterministic decision ids + exact-once gate (pure module, tested) and an opt-in repeat guard exist but are wired into no order path; M3-B must back the gate with a durable unique constraint and reconcile by client id (F-12). Note: an id makes RE-SUBMISSION of one decision harmless; it does not merge a signal re-emitted on a later bar (that is the repeat guard's job) |
| T-15 | Compromised dependencies / supply chain | locked dependencies (`uv.lock`, `pnpm-lock.yaml`), pip-audit and pnpm audit clean, secret scan, no install scripts run in the boundary (proxy and probe use only stdlib on the stock python image) | audits in acceptance doc | the base image `python:3.12-slim` is pulled by tag, not digest (F-13) |
| T-16 | Malicious AI-generated instructions / prompt injection into operators or code | AI/strategy principals can never lease credentials (tested); RBAC has no order permission; research package is pure | vault principal tests | human review of any AI-written change to allowlist/privilege config still required (policy) |
| T-17 | Audit-log tampering | hash-chained append-only `audit_events` (M0), access-log chain in the vault contract | chain verification tests | proxy decision log is a container log, not chained (F-07) |
| T-18 | Operator privilege misuse | MFA for operator/reviewer/admin, RBAC deny-by-default, lockout, audit of actions; signed 2-reviewer config for any future execution privilege | M0 auth tests, privilege-config test | reviewer key custody undefined (F-06) |
| T-19 | Configuration drift (allowlist, compose, profiles) | allowlist/compose in Git, allowlist SHA-256 logged at start, compose posture test, profile restore verified | `test_allowlist_*`, `test_compose_hardening_*` | no automated drift detector against the running container (F-07) |

## 2. Attacker-oriented negative test plan
Executed (internal): the 78 checks of `infra/egress/negative_tests.py` (raw sockets from inside the container, so application logic is bypassed), resolver abuse unit tests, binding abuse (9 override shapes), vault abuse (all non-connector principals, forbidden permissions, revoked/expired, tamper), logging/crash exposure, repository secret scans.
Not executed / for the independent reviewer: container-escape and kernel exploitation, Docker Desktop reboot persistence, TLS interception of the CONNECT tunnel, race conditions in lease/revoke under concurrency, fuzzing the proxy parser beyond the cases listed, side channels (timing of denial vs allow), supply-chain verification of the base image digest, a live OKX demo check of the header behaviour.

## 3. Findings register

Severity scale: **Critical** (credentials or live trading exposed now), **High** (would allow bypass of a mandatory control or must be closed before credentials), **Medium**, **Low**, **Info**. No finding is Critical. Owner = who must close it.

| ID | Sev | Finding | Owner | Closure condition | Status |
|---|---|---|---|---|---|
| F-01 | High | Legacy M2 host processes (supervisor, paper service) are not inside the egress boundary; their egress is application-level only | Engineering (M3-B / ops) with CEO approval for relaunching M2 services | the only process that can hold credentials (connector) runs inside the protected runtime and the negative tests pass from that container; legacy services either containerised or accepted as data-only with no credentials | **Open; blocks credential readiness** |
| F-02 | High | The boundary is not independently enforced by a dedicated VM. **R1:** the proxy's egress is now additionally bound by kernel netfilter rules in a separate container (default DROP, resolved-address allowlist, allowlist-only DNS), verified with 39 compromised-proxy checks; but the gate shares the Docker Desktop kernel and VM with all other projects | Engineering + independent reviewer | enforcement at a dedicated VM/firewall that permits only allowlisted destinations (design and plan in `VM-BOUNDARY-DESIGN.md`; not provisionable on this PC: Windows Home, no admin rights, no hypervisor software), or the reviewer formally accepts the residual with compensating controls | **Open (mitigated, not closed); blocks credential readiness** |
| F-03 | High | No independent security review has been performed | CEO (appoint reviewer) | written report from a qualified reviewer independent of the implementer, all High/Critical findings closed or accepted | **Open; blocks credential readiness** |
| F-04 | Medium | OKX demo and production share the REST host; environment is selected by a header and credential class (DOCUMENTED excerpt, not runtime verified; OKX unreachable here) | Engineering (M3-B) | runtime verification with a real demo key in an authorized M3-B: header behaviour, `50101` handling, response indicators; fatal alarm path tested | Open |
| F-05 | Medium | Vault is an in-process contract; no real backend, no process separation, key custody undefined | Engineering + reviewer | backend selected and reviewed; connector cannot enumerate other identities; custody and rotation runbook | Open |
| F-06 | Medium | Reviewer public-key set and custody for signed privilege configs undefined | CEO/Engineering | named reviewers, key generation ceremony, rotation/revocation procedure | Open |
| F-07 | Medium | Proxy denials are only container logs (no audit-chain entry, no alert); no drift detection of the running containers vs Git | Engineering | decisions shipped to the audit chain, alert on repeated DENY, periodic hash check of allowlist/compose | Open |
| F-08 | Low | `data/manual/creds.json` holds plaintext seeded app-user logins (viewer1/op1/rev1) from M0 manual testing; git-ignored, never committed, not exchange credentials. **R1:** determined OBSOLETE (users exist in no database), no workflow dependency, 0 leaks of its values in tree or history (`CREDENTIAL-HYGIENE.md`) | Engineering | owner deletes the file (command in `CREDENTIAL-HYGIENE.md`); hash-pinned test guards against new plaintext credential files | Open (deletion was denied by the sandbox policy; no workaround attempted) |
| F-09 | Medium | A malicious *public* DNS answer for an allowed name would be accepted by the proxy (no DNSSEC/pinning); client TLS validation is the remaining control | Engineering + reviewer | decide on certificate/SPKI pinning or static address validation for connector hosts | Open |
| F-10 | Low | Redirect behavior of the `websockets` client library not verified | Engineering | test or pin `follow_redirects=False` behavior for the installed version | Open (policy still refuses unauthorized hosts) |
| F-11 | Low | `httpx` `trust_env` honours `HTTP(S)_PROXY` in legacy host processes | Engineering | construct clients with `trust_env=False` in the connector and require the fixed proxy | Open |
| F-12 | Medium | Strategies re-emit identical rejected intents (95 % repeats); no idempotent client order IDs for an authenticated venue yet | Engineering (strategy owner) | M3-B design: deterministic client order IDs, signal de-duplication, exposure-aware suppression; tested before any demo order | Open |
| F-13 | Low | Base image tagged (`python:3.12-slim`), not pinned by digest | Engineering | pin digest, rebuild provenance noted | Open |
| F-14 | Low | Boundary persistence across Docker Desktop/VM restart and Windows reboot not exercised (service, container and gate restarts and the fail-closed case ARE verified in R1; a VM restart would interrupt all other projects) | Engineering | reboot test recorded | Open |
| F-15 | Info | 29 pyright findings in tests/scripts (type-only) | Engineering | optional cleanup | Open (carried from M2) |

**Critical findings: 0. High findings: 3 (F-01, F-02, F-03), all open and all blockers to credential readiness.**


## 4. R1 additions
| ID | Sev | Finding | Owner | Closure condition | Status |
|---|---|---|---|---|---|
| F-16 | Medium | Accepted-evidence integrity: (a) e2e runs overwrote the frozen M2 screenshots, (b) historical `*.log` evidence files were git-ignored and untracked (not in the tags) | Engineering | screenshots separated per milestone/run and refused for frozen folders; manifest + verifier + regression tests; logs tracked and hash-pinned | **Closed in R1** (`FROZEN-EVIDENCE.json`, `scripts/verify_frozen_evidence.py`, `tests/m3a/test_frozen_evidence.py`) |
| F-17 | Low | The single gitleaks fingerprint exception (synthetic test string in history) | Engineering | reviewed: suppresses exactly one finding, justified, rewriting history would invalidate hashes | **Reviewed, accepted** (`CREDENTIAL-HYGIENE.md` section 3) |
| F-18 | Medium | The in-Docker gate container holds `NET_ADMIN` (scoped to its namespace) on a kernel shared by all projects | Engineering + reviewer | superseded by the dedicated VM boundary | Open (part of F-02) |

Status after R1: **Critical 0; High 3 open (F-01, F-02 mitigated-not-closed, F-03).** Credential readiness: **NO-GO**.


## 5. R2 status changes
| ID | Change |
|---|---|
| F-08 | **Closed in R2**: obsolete file deleted, absence verified, tombstone test added (`CREDENTIAL-HYGIENE.md` R2) |
| F-17 | Reviewed again: two single-fingerprint exceptions, both demonstrably synthetic, suppression list pinned by test |
| F-02 | Linux security-host plan and config artifacts finalized and `nft -c`-validated (`VM-BOUNDARY-DESIGN.md` R2, `infra/security-host/`); still OPEN until deployed and independently verified |
| F-05/F-06 | Vault placement and privilege boundaries designed (separate users, `PrivateNetwork=yes` vault, unix socket, key custody); backend selection and key ceremony still open |
| F-07 | Monitoring/backup/restore plan written (`VM-BOUNDARY-DESIGN.md` R2-4); implementation still open |

Remaining High after R2: **F-01, F-02, F-03** (unchanged count). Critical: 0. Credential readiness: **NO-GO**.


## 6. R3 status
F-01, F-02, F-03: **OPEN** (no dedicated infrastructure, no reviewer). Deployable artifacts, acceptance controls C1..C10, F-01 policy tests, M2 rehearsal and a consolidated register (`RESIDUAL-RISK-REGISTER.md`) were added. New disclosed item OP-2 (a test started the WSL launcher). Full-history gitleaks currently reports 3 unsuppressed findings (SHA-256 digests in the R2 review manifest, not secrets) awaiting an owner decision. Credential readiness: **NO-GO**.


## 7. ASTA-M3A-MAC-01 update
The CEO chose the existing Mac mini M4 (macOS host + Linux ARM64 VMs) as the dedicated host (`MAC-MINI-DEPLOYMENT-DESIGN.md`). New finding F-19 (macOS host is the trust base; Phase A not yet performed) and R-X (cross-architecture determinism unverified) are in `RESIDUAL-RISK-REGISTER.md`. F-01, F-02, F-03 remain **OPEN**; credential readiness **NO-GO**.
