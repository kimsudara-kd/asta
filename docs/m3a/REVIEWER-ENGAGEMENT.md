# Independent reviewer engagement kit (M3A-R3, for the CEO)

The gate F-03 closes only when a **qualified reviewer independent of the implementer** performs a real review. This kit lets the CEO commission one. The implementer (Claude) cannot designate, instruct or sign for the reviewer.

## 1. Reviewer qualification (suggested minimum)
* Independent of ASTA's implementation and of the AI assistant that produced it (no authorship of any M3-A artifact; no financial interest in the outcome).
* Demonstrable experience in at least three of: Linux network/firewall engineering (nftables), container/VM isolation, applied cryptography and secrets management, threat modelling/penetration testing of network boundaries, financial-API (exchange) security.
* Willing to sign a written report and a statement of independence; able to work with the repository read-only plus the test commands in `REVIEW-INDEX.md`.

## 2. Scope of the engagement
In scope: the artifacts and claims in `INDEPENDENT-REVIEW-PACKAGE.md` (egress gate and proxy, dedicated-host design and configuration, credential vault contract, environment binding, signed-configuration scheme, threat model, findings F-01..F-18), execution of the negative-test suites, and - once it exists - the dedicated host (acceptance controls C1..C10 in `VM-BOUNDARY-DESIGN.md` R3 including a real reboot matrix).
Out of scope unless separately authorized: any exchange account or key, any authenticated exchange call, the Windows host and other projects on it, denial-of-service testing of third parties, social engineering.

## 3. Rules of engagement (to be countersigned by the CEO)
1. Read-only access to the repository; no credentials are provided or created for the review.
2. Active tests only against ASTA-owned test infrastructure (the Docker boundary now; the dedicated host later). **No testing of other projects' containers, networks or host settings.**
3. Any destructive or host-affecting test (hypervisor, firewall, reboot) requires the CEO's written approval beforehand.
4. Findings are reported privately to the CEO first; severities use Critical/High/Medium/Low/Info as in `THREAT-MODEL.md`.
5. The implementer fixes findings; the reviewer re-tests and records closure; the implementer never marks a finding closed on the reviewer's behalf.

## 4. Questions the reviewer must answer explicitly
The 13 questions in `INDEPENDENT-REVIEW-PACKAGE.md` (1-13), plus: (14) are acceptance controls C1..C10 sufficient and are any missing; (15) is Option A (separate small Linux host, `HOST-INVENTORY-AND-FEASIBILITY.md`) the minimum acceptable form for F-02, or is a cloud pair acceptable; (16) is the proposed Binance-only first scope (`M3B-READINESS.md` R2) acceptable from a security standpoint.

## 5. Deliverable
A signed report: reviewer identity and independence statement, commit and manifest hash reviewed, methodology, findings (severity, evidence, reproduction, remediation), answers to the questions, a **credential-readiness opinion (ready / not ready / ready with conditions)**, and later a closure statement after remediation. Record it under `docs/evidence/m3a/independent-review/` and update the sign-off block in `INDEPENDENT-REVIEW-PACKAGE.md`.

## 6. Status
**No reviewer designated. No review performed. Reviewer findings recorded: none.** Gate F-03: OPEN.
