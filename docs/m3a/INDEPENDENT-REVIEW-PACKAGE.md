# Independent security review package (for a separate qualified reviewer)

**Status: independent review OUTSTANDING.** The implementing engineer (Claude) wrote all code and documents in M3-A and **cannot certify them**. Per the milestone directive this is an explicit **blocker to credential readiness (M3-B)**. This package is what a reviewer needs; nothing in it is a review result.

Reviewer independence requirements: not the author of any M3-A artifact, security qualification (network/container security, applied cryptography, threat modelling), access to the repository at the review commit, and written report format below. The reviewer should be appointed by the CEO.

## 1. Review commit and how to obtain it
Repository `E:\Dev\asta` (no remote). Review commit: the final M3-A commit recorded in `docs/evidence/M3A-acceptance.md`; baseline `fe8194c` / tag `m2-accepted-with-residual`. Change inventory since the baseline: `git diff --stat fe8194c..HEAD`.

## 2. Artifacts in scope
| Area | Files |
|---|---|
| Egress boundary | `infra/egress/docker-compose.egress.yml`, `infra/egress/proxy.py`, `infra/egress/allowlist.json`, `infra/egress/negative_tests.py`, `infra/egress/capability_probe.py` |
| Credential/environment contracts | `src/asta/security/{redaction,vault,binding}.py`, `src/asta/security/__init__.py` |
| Tests | `tests/m3a/test_egress.py` (37), `tests/m3a/test_security.py` (40), existing `tests/live_lock/*`, `tests/contracts/*` |
| Pre-existing controls (re-read for context) | `src/asta/core/egress.py`, `src/asta/core/settings.py`, `src/asta/core/environment.py`, `src/asta/risk/gateway.py`, `src/asta/exchanges/common/{http,ws}.py`, `src/asta/auth/*`, `src/asta/audit/chain.py` |
| Documents | `docs/m3a/*.md`, `docs/evidence/M3A-acceptance.md`, `docs/evidence/m3a/*` |

## 3. Reproduction commands
```bash
# boundary up (own compose project; touches no other project)
docker compose -f infra/egress/docker-compose.egress.yml up -d
# negative tests from INSIDE the restricted container (78 checks)
MSYS_NO_PATHCONV=1 docker exec asta-protected-probe python /app/negative_tests.py
# proxy decision log (every ALLOW/DENY with reason)
docker logs asta-egress-proxy
# unit + integration tests for M3-A, then everything else
python -m pytest tests/m3a -q ; ASTA_RUN_NETWORK_TESTS=1 python -m pytest -q
# static
ruff check . && ruff format --check . ; mypy (container, see README) ; pyright src
# secrets
python scripts/secret_scan.py ; gitleaks git . -c .gitleaks.toml --redact
# container posture
docker inspect asta-egress-proxy asta-protected-probe   # compare docs/evidence/m3a/container-posture.txt
```
Capability probes (public, unauthenticated, only with the `capability_probe` profile: `ASTA_EGRESS_PROFILE=capability_probe docker compose -f infra/egress/docker-compose.egress.yml up -d`, then `docker exec asta-protected-probe python /app/capability_probe.py`); restore with the plain `up -d`.

## 4. Questions the reviewer is asked to answer
1. Is an internal Docker network plus a hardened CONNECT proxy an acceptable boundary on Docker Desktop, or must the boundary be a separate VM/firewall (finding F-02)? Is the proxy container's NAT egress acceptable with its compensating controls?
2. Can any request from the protected container reach a non-allowlisted destination (parser edge cases: absolute-form targets, whitespace, header smuggling, IDN/punycode, very long names, port zero, CRLF)?
3. Is refusing any non-public address in a DNS answer sufficient against rebinding, and is the accepted residual F-09 (malicious public answer) tolerable, or is pinning required?
4. Are the vault contract, the permission model and the grant/lease flow sound for a connector design? What process/key-custody separation is required for the real backend (F-05)?
5. Is the 2-of-N Ed25519 reviewer signature scheme adequate for authorising execution privileges (F-06)? Replay protection, expiry and revocation of signed configs are not designed yet.
6. Does the OKX shared-host design (header + credential class + fatal `50101`) provide adequate demo/production separation, given it could not be runtime verified (F-04)?
7. Are there paths by which strategies, AI modules or the UI could reach a credential or the vault?
8. Is the audit design (hash chain, proxy log) adequate for detecting credential misuse; what must be shipped/alerted (F-07)?
9. Duplicate-order controls required before any demo order (F-12).
10. Anything the implementer missed: please attack the work, including `THREAT-MODEL.md` assumptions.

## 5. Known weaknesses disclosed up front
See `docs/m3a/THREAT-MODEL.md` findings F-01..F-15. The most important: legacy M2 host processes are outside the boundary (F-01); the proxy's own egress is not independently firewalled (F-02); OKX could not be reached so its demo behaviour is unverified (F-04); the vault has no real backend and no process isolation (F-05).

## 6. Expected deliverable from the reviewer
A signed report with: scope and commit reviewed, methodology, findings with severity (Critical/High/Medium/Low/Info), reproduction steps, recommended remediation, a statement on each of questions 1-10, and an explicit **credential-readiness opinion (ready / not ready / ready with conditions)**. Closure of Critical/High findings is then tracked in `THREAT-MODEL.md` (owner and closure condition per finding).


---

# R1 update: completeness of the package and reviewer status

**Reviewer designation: NOT RECEIVED. Independent review gate: OPEN.** No qualified independent reviewer has been designated by the CEO and no review has been performed. The implementer's own testing and this package are not an independent review and are not presented as one.

## Package contents (all in the repository at the final R1 commit)
| Required item | Where |
|---|---|
| Network and trust-boundary diagrams | `TRUST-BOUNDARIES-AND-LIFECYCLE.md` section 1, `VM-BOUNDARY-DESIGN.md` section 3 |
| Threat model, findings, owners, closure conditions | `THREAT-MODEL.md` (F-01..F-18) |
| Egress policy and negative-test results | `EGRESS-ENFORCEMENT.md` sections 3, 4, 8; `docs/evidence/m3a/negative-tests-run*.json`, `r1-negative-tests-gated-run1.json`, `r1-gate-tests-run1.json`, `r1-boundary-verification.json`, `container-posture.txt` |
| Authentication and authorization controls | `TRUST-BOUNDARIES-AND-LIFECYCLE.md` section 2 |
| Mock vault isolation | `CREDENTIAL-ISOLATION.md`, `tests/m3a/test_security.py` |
| Credential lifecycle design | `TRUST-BOUNDARIES-AND-LIFECYCLE.md` section 4 |
| Demo/production environment-separation design | `TRUST-BOUNDARIES-AND-LIFECYCLE.md` section 5, `CREDENTIAL-ISOLATION.md` section 3, `EXCHANGE-CAPABILITY-MATRIX.md` |
| Known vulnerabilities and unresolved findings | `THREAT-MODEL.md` section 3-4 |
| M0-M2 safety regression evidence | `docs/evidence/M3A-acceptance.md` section 2 (full suite, live-lock, browser, diffs, frozen-evidence verifier) |
| Remaining attack paths | `TRUST-BOUNDARIES-AND-LIFECYCLE.md` section 6 |
| Dedicated-VM architecture, sizing, deployment plan | `VM-BOUNDARY-DESIGN.md` |
| Credential hygiene and gitleaks exception review | `CREDENTIAL-HYGIENE.md` |

## New reproduction commands (R1)
```bash
python infra/egress/verify_boundary.py out.json         # baseline/restart/gate-restart/fail-closed/recovery matrix
docker exec asta-compromised-proxy-sim python /app/gate_tests.py   # unprivileged attacker inside the proxy namespace
python scripts/verify_frozen_evidence.py                # frozen M0-M2 evidence integrity
```
Additional reviewer questions: (11) is the gate-sidecar pattern (proxy in the gate's namespace, NET_ADMIN confined to the gate) acceptable as an interim boundary or must the VM exist first? (12) is the resolved-address allowlist (public IPv4 only, 60 s refresh, 10 min expiry) adequate against address rotation, and is the dnsmasq-from-hosts-file design sound? (13) should the legacy M2 host processes be moved behind the same boundary or kept strictly credential-free by policy and test?
