# ASTA-M3B-R4: dedicated Mac security-host preparation (2026-11-14)

**Final status: BUILT — NOT ACCEPTED.** Credential readiness: **NO-GO.** Baseline `c10da4a`. Preparation only: no `sudo`, no host security change, no provisioning, no credential (real/demo), no authenticated order, no signing-code activation, no live-lock change, no push, no acceptance tag, no migration of unrelated projects and no disruption of M2 paper sessions. Independent approval is **unsigned**. F-01, F-02, F-03 and H-1 remain unresolved.

## Deliverables
| # | Deliverable | Location |
|---|---|---|
| 1 | Unrelated-project migration inventory | `docs/m3b/HOST-DEDICATION-INVENTORY.md` (+ `docs/evidence/m3a/r4-host-separation-baseline-macos.json`) |
| 2 | Mac host security checklist | `docs/m3b/HOST-SECURITY-CHECKLIST.md` (+ `infra/security-host/mac/sshd-asta.conf`, `qemu_pin.py`, `qemu-pin.{sha256,json}`) |
| 3 | Storage and RAM capacity assessment | `docs/m3b/CAPACITY-ASSESSMENT.md` (+ `r4-two-vm-load-test-macos.json`) |
| 4 | Two-VM network-isolation validation plan | `CAPACITY-ASSESSMENT.md` s.5-6 (+ `r4-netns-lab-aarch64.json`, 38/38) |
| 5 | Vault compatibility decision matrix | `docs/m3b/VAULT-FEASIBILITY.md` |
| 6 | Backup-key replacement procedure | `docs/m3b/VAULT-AND-BACKUP.md` s.4 + `infra/security-host/mac/backup-key-replace.sh` (tested) |
| 7 | Security-event and bridge supervision design | `docs/m3b/SECURITY-EVENTS-AND-ALERTS.md` + installer `install-security-services.sh` |
| 8 | Updated outstanding findings | s.3 below and `docs/m3a/RESIDUAL-RISK-REGISTER.md` |
| 9 | Full regression results | s.2 |
| 10 | GO/NO-GO | s.4 |

## 1. What was done (highlights)
* **Inventory and restorability (read-only):** 24 unrelated containers (5 running, 2 publishing on all interfaces), 88 volumes, Docker data disk 50.6 GiB, LAN-reachable SSH/SMB; two running PostgreSQL databases dumped and **restored into scratch with exact table and row equality** (57 tables/3,788 rows; 129 tables/412,096 rows), an SQLite volume integrity-checked; scratch data deleted. **Disclosure D-0:** my R2 start of Docker Desktop restarted these containers; nothing was touched in R4.
* **Host security preparation:** FileVault + recovery-key escrow plan, controlled firewall activation, account-separation review (decisions D-B1..D-B3), SSH key-only drop-in prepared, **QEMU pinned** (31 files, binary + firmware + dylib closure; provisioning refuses a stale pin; `qemu-vms.sh run` refuses to start without a matching pin: exit 70 before any QEMU process).
* **Two-VM architecture:** provisioning default is now fw + app (`--with-cred` for the later phase); `qemu-vms.sh` sizes 768 MiB / 3 GiB, credential VM refuses to start (exit 71) and the fw VM has no cred link by default; tests assert no VM has a host-network, share, USB or forwarding device.
* **Capacity and load:** storage budget (does not fit internally today; fits after the Docker data leaves or on a quota'd APFS volume), RAM measurements, **two scratch VMs under load** with host metrics.
* **Network isolation:** `netns_lab.py` extended by 16 bypass checks (IPv6, metadata, RFC1918, CGNAT, DoT/TCP-53/QUIC, alternate routes, no second NIC, own-drop counters) and run on aarch64 Linux 6.8: **38/38 passed**. (First run of the new code found a flawed check of mine, expecting the fw counter to move although the app's own firewall dropped first; fixed.)
* **Vault feasibility from official docs:** Binance (HMAC/RSA/Ed25519; Ed25519 recommended; public key registered) and OKX (HMAC + passphrase header; demo header) verified; OpenBao transit fits a Binance Ed25519 key only: **no `hmac` import documented**, so OKX secrets and passphrase can only be stored.
* **Engineering corrections:** **G-10 closed:** migration `0011`, `release_kill_switch(reason)` uses `session_user`; manual authorisation (listed releaser, empty by default) and the >= 10 character reason are unchanged; refusals audited with the authenticated login; role tests use `SET SESSION AUTHORIZATION`. **Monitor audit:** `security.alert_state` appended to the hash chain on alert-state changes only. **Installer** for the bridge/monitor/roles in the app VM (dry run, refuses on macOS). **Host-separation check** (read-only, 14 checks; baseline FAIL 10/14). **gitleaks review:** the five stale `.gitleaksignore` fingerprints are exact `commit:file:rule:line` entries for SHA-256 digests and one synthetic string; gitleaks 8.30.1 reports none with them removed, and a planted fake key repo is detected (2 leaks), so detection is intact; entries **kept** until a CI run with the now-pinned `GITLEAKS_VERSION: 8.30.1` confirms; no suppression broadened.

## 2. Regression (offline; Docker boundary containers up)
See the "Results" table at the end of this file (filled from the final run).

## 3. Outstanding findings and owners
| ID | Sev | Status | Owner / next step |
|---|---|---|---|
| F-01, F-02, F-03 | High | **OPEN** | CEO (provision, reviewer, credential VM later) |
| H-1 | High-adjacent | **OPEN**; inventory + plan + check ready | CEO: decide D-A, approve migrations |
| **D-0** (new) | Med | **OPEN, urgent**: unrelated containers running because of my R2 Docker start; two ports on all interfaces; P1 tunnel not running | CEO: quit Docker / bind to loopback / fix tunnel |
| H-3 (new) | Med | SSH, SMB, 88 reachable on the LAN; auto-login on; firewall off; FileVault off | CEO: D-B2, D-B3, D-F; execution window |
| D-F | decision | FileVault vs unattended restart | CEO |
| G-10 | Med | **CLOSED** (migration 0011) | |
| P-5 | Med | partly closed (pin + refusal); owner regenerates at provisioning | CEO/Eng |
| H-2 | Med | capacity: documented, fits after migration or on a quota'd volume | CEO |
| F-05/F-06 | Med | OpenBao-transit + Binance Ed25519 recommended; OKX out of scope; rehearsals not run | CEO + reviewer |
| key exposure | Med | replacement helper ready; not performed | CEO (ceremony) |
| F-07 | Med | monitor -> audit done; shipper + off-host alerts designed, not built | Eng |
| R-D1b | Med | installer ready, not run (no VM) | Eng at provisioning |
| S-2 | Low | analysed; kept until CI (pinned) confirms | CEO |
| G-08, R-2 | Med | push identity, hosted CI never run | CEO |
| G-04 residual | Low | pre-auth key-id parse bounded; audience binding closed in R2 | |
| G-11 | Low | accepted | |

## 4. M3-B readiness: **NO-GO**
All preparation items are complete and tested offline; the host is not dedicated, not provisioned, still has unrelated listeners and containers, has no independent reviewer, no vault backend, no key ceremony, no off-host alert, no hosted CI run and no authorisation. **BUILT — NOT ACCEPTED.**

## Results (final run, offline, Docker boundary containers up; not deployed-host evidence)
| Check | Result |
|---|---|
| Python suite (PostgreSQL 16, UTC) | **1,334 passed, 5 skipped, 0 failed** (all 8 Docker/firewall gates ran; the 5 skips are the classified non-security ones: 4 opt-in network tests, 1 obsolete PowerShell parse). An earlier run found 1 failure of mine: a 64-hex Homebrew bottle digest in `qemu-pin.json` tripped the repository's key-shape scan; the metadata format was changed (digest now follows a `sha256` key) and the run above is clean |
| ruff check/format, mypy (122 files) | clean |
| pip-audit; `pnpm audit --prod`; `tsc`; web unit tests (48) | no known vulnerabilities; no known vulnerabilities; clean; pass |
| gitleaks 8.30.1 full history; `scripts/secret_scan.py` | no leaks / 0 findings (see the commit check); detection sanity: a planted fake-key repo is detected with the repo config |
| M0-M2 frozen evidence; paper ledger | intact; 900/900 checkpoints, 0 discrepancies, fills digests unchanged (`f9d8e770d765ee84`, `be76eb9ef1a917a5`) |
| Live-lock paths vs `fb7770b` | unchanged |
| Browser (13-page structural check on the restarted dev stack at migration 0011) | ALL OK |
| Dev database | migrated to `0011` (additive; no paper table touched; no session migrated); dev stack restarted |
| Executed on scratch VMs (user level) | netns lab **38/38**; two-VM load test (saved JSON) |
| Not executed (needs the approved window / provisioned host) | provisioning, FileVault, firewall, SSH change, `host-separation-check.sh` target state, H1-H8, B1-B10, C1-C10, R1-R10, off-host alert drill, vault rehearsals |
Playwright e2e suite itself was not re-run in R4 (unchanged UI; last full run 39/39 in R2).
