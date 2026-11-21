# M3-B independent review — report and disposition (2026-11-21)

Reviewer: an AI review agent appointed by the owner ("create agent that have skill review"), read-only, separate context from the builder.
It is an independent *code* review; it did **not** examine the deployed host, VMs or live drills, and it is not a human sign-off.
Gate G-3 therefore records: **review performed (AI reviewer, code only); owner sign-off pending.** F-03 stays OPEN until the owner records acceptance.

**Verdict: ACCEPT WITH CONDITIONS** as an offline engineering acceptance candidate; **not** acceptable for credential/demo readiness.
Verified by the reviewer in code: ExecutionGateway constant DENY, order_gate EXTERNAL constant DENY, GatedExchange/Dispatcher refuse raw/non-simulated ports,
exactly-once claim/write-ahead/resend, idempotent fills, channel authenticates before parsing, live-lock files unchanged vs `fb7770b`. Reviewer ran 622 tests (all pass).

| ID | Sev | Finding (short) | Disposition |
|---|---|---|---|
| R-01 | High | kill-switch release guard satisfiable by setting a GUC | **FIXED in code**: migration 0012 (guard also needs a listed releaser as `session_user` and a same-transaction RELEASE event); test added. Primary control (revoke UPDATE from all but the definer owner) stays in `pg_roles_m3b.sql`, still undeployed → host step |
| R-02 | High | `asta_monitor` lacked SELECT on incidents / market_feed_connections | **FIXED**: grants added; test runs the monitor queries under the real role |
| R-03 | Med | Notifier prefix-matched URLs, followed redirects | **FIXED**: parsed host check, no credentials in URL, redirects disabled; test |
| R-04 | Med | per-human `rel_*` logins vs hba/ident (single releaser) | OPEN: decide shared releaser identity vs hba lines for each releaser (owner decision before deployment) |
| R-05 | Med | `WatchdogSec` without sd_notify would restart the bridge | **FIXED**: removed (DB heartbeat is the liveness control) |
| R-06 | Med | fill digest covered ids only | **FIXED**: per-session content digest (id, side, symbol, price, qty, fee, notional, pnl) pinned and verified; id digests still match frozen M2 |
| R-07 | Med | privacy scan fail-open | **FIXED**: git failure or empty map → exit 2; test |
| R-08 | Med | firewall allow-set from unauthenticated DNS; IP-only nft layer | OPEN (design, F-02): pin ranges / DoT before VM deployment |
| R-09 | Low | proxy `IPAddressAllow=0.0.0.0/0`, listens on 0.0.0.0 | OPEN (F-02) |
| R-10 | Low | dead-man MAC key on the app VM; replay ≤120 s; URL with query string | ACCEPTED/documented limit |
| R-11 | Low | signer deny-list not allow-list; key attribute reachable | ACCEPTED for a test double; allow-list required before any real signer |
| R-12 | Low | no import-graph rule for network use in `orders` | OPEN: add with the signer-placement decision |
| R-13 | Low | `asta_orders` can lower the nonce high-water | ACCEPTED (trusted role); insert-only function later |
| R-14 | Low | `SqlKillSwitch.active()` raises on DB error | ACCEPTED (fail-closed) |
| R-15 | Info | stale acceptance text; `history_scrub.py` had no scratch guard | **FIXED**: acceptance refreshed; script requires `SCRUB_CONFIRM_SCRATCH=1` |

Open High/Critical after this round: F-01, F-02, F-03, H-1, H-3 (R-01/R-02 closed in code but their deployed-role proof needs the VM/host steps).
