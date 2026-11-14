# M3-B gate checklist: what only the owner can do (2026-11-14)

Everything that can be built and proven **offline** is built (`ORDER-PATH-DESIGN.md` s.7, `docs/evidence/M3A-acceptance.md` MAC-A15). What remains is not code. Each line names who does it and how closure is shown. **M3-B is still NOT authorized; no credential, connector, signing code or order exists.**

| # | Gate | Needs | Owner action | Shown closed by |
|---|---|---|---|---|
| B-1 / F-03 | Independent security review | a person | appoint a reviewer, hand over `docs/m3a/INDEPENDENT-REVIEW-PACKAGE.md` | written report; Critical/High closed or formally accepted |
| B-3 / F-02 | Boundary enforced outside the app host | `sudo` on the Mac mini | review then run `sudo infra/security-host/mac/provision-host.sh --apply` (dry run first; see MAC-A8/A9), then the reboot matrix | acceptance suite C1..C10 + reviewer sign-off |
| B-2 / F-01 | Legacy processes stay credential-free | a decision | decide in writing: the connector lives only inside the protected runtime | negative tests re-run from the connector's own container |
| B-4 / F-05,F-06 | Real vault backend and key custody | a decision + ceremony | choose a backend; move `~/asta-backup-identity/identity.txt` OFFLINE (MAC-A12) | reviewed backend, key ceremony record |
| B-5 | OKX | network path + docs re-read | leave OKX out of the first scope (proposed) | recorded scope decision |
| B-7 | Runtime rule/fee verification, hosted CI | build work after authorization | first M3-B tasks | CI run, runtime `exchangeInfo` check |
| U-1..U-4 | Real exchange behaviour (id reuse after fill, query visibility, id rules, resets) | a testnet key | create it yourself (READ + SPOT TRADE only, **never withdrawal**, never in chat or git), hand it straight to the approved vault | measured results appended to `ORDER-PATH-DESIGN.md` s.5 |
| Directive | Fresh M3-B authorization with its own gates | your signature | write it; it must also authorize changing the live-lock tests that forbid signing code | `docs/decisions/ceo-approval-register.md` entry |

Order of work once you decide: B-1 and B-3/F-02 first (they gate everything), then B-4, then the directive, then the connector build (`ExchangePort` and `Authenticator` implementations behind the existing ports), then U-1..U-4 on the testnet.
