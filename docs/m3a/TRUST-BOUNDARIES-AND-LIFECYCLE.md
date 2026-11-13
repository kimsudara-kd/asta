# Trust boundaries, access control, credential lifecycle and environment separation (M3A-R1 Workstream B)

Part of the independent-review evidence package (`INDEPENDENT-REVIEW-PACKAGE.md`). Written by the implementer; not a review.

## 1. Network and trust-boundary diagram (as built)

```
 ZONE 0  Internet (untrusted)
   ^  only allowlisted public names/ports (TLS opaque)
   |
 ZONE 1  asta_egress  172.31.251.0/24  (Docker NAT bridge; only the gate is attached)
   |
 +-------------------------- egress-gate NETWORK NAMESPACE (trust boundary B2) -----------------------------+
 |  nftables: input/output/forward DEFAULT DROP, IPv6 dropped; allow only TCP -> gate-resolved public IPs on 443/9443 |
 |  dnsmasq: answers ONLY allowlisted names from gate-resolved data; everything else REFUSED                  |
 |  egress-proxy (python, uid 65534, no caps, read-only fs): CONNECT-only, name+port allowlist, IP-literal refusal,    |
 |                 resolves->public-IPv4-only check, pinned connect, JSON decision log                          |
 +-----------------------------------------------------------------------------------------------------------+
   ^  TCP 3128 from 172.31.250.0/24 only
 ZONE 2  asta_protected  172.31.250.0/24  (Docker INTERNAL network: no gateway, no NAT, no IPv6)   [boundary B1]
   ^
 protected runtime (future connector; today: asta-protected-probe)  non-root, no caps, read-only fs, no host mounts
   |  (future) vault lease  <-- in-process contract today (F-05)

 ZONE 3  Windows host (legacy, unchanged, NOT protected): paper supervisor + paper service (public data direct),
         PostgreSQL containers on 127.0.0.1, API/UI on demand. Holds NO credentials. Cannot be reached from zone 2.
 ZONE 4  Other projects' containers/networks on the same Docker Desktop VM: not touched, not reachable from zones 1-2
         by design (separate bridge networks; not tested by connecting to them).
```
Boundary B1 (Docker internal network) stops direct egress of the runtime; B2 (netfilter in the proxy's namespace) stops egress of the proxy itself. Both live in the Docker Desktop kernel, which is why a dedicated VM remains required (VM-BOUNDARY-DESIGN.md).

Application-level trust zones inside ASTA (unchanged, tested): `core` (no ASTA imports) -> `audit` -> `risk` (gateway default DENY) -> `research` (pure) / `paper` / `market_data` / `exchanges` (public data only). New `security` package is imported by nothing (test) and imports only `core`/`audit`.

## 2. Authentication and authorization controls (evidence: M0 tests, re-run in the M3-A full suite)
* Passwords: Argon2id, OWASP-minimum parameters enforced (cannot be lowered). TOTP MFA mandatory for operator, reviewer and admin roles; unknown roles are treated as MFA-required.
* Sessions: opaque, server-side (database), 30-minute TTL, CSRF token per session (X-CSRF-Token, fixed for the sign-out race in M2-R4), account lockout with audit events.
* Authorization: static role-permission map, deny by default; viewer (view), operator (halt, research, import, paper control), reviewer (audit view), admin (all). **No role has any order, credential or exchange-connection permission**; no permission for those exists.
* Audit: hash-chained append-only `audit_events` (verification tested; chain verified intact through M2), plus the vault access-log chain contract.
* Gaps: cookies are `Secure=false` in local development; there is no network-level authentication of the API (localhost only); reviewer/admin accounts live in the application database (F-06 covers key custody for signing privileges, not logins).

## 3. Mock-vault isolation (M3A-2; tests `tests/m3a/test_security.py`, 40)
Separate identities (`vault/binance/spot-testnet`, `vault/okx/demo`), demo-only credential classes, permissions limited to READ and SPOT_TRADE (withdrawal, transfer, margin, futures, borrowing, sub-account and account-management refused), lease only by an explicitly granted CONNECTOR principal (strategies, AI modules, analytics, operators and the UI always denied), short leases, masked `SecretValue`, scrubbed logs/exceptions/crash text, encrypted-at-rest file backend with tamper detection, hash-chained access log without secret material. Import-boundary tests prove no other package can import it.

## 4. Credential lifecycle design (future, NOT implemented beyond the contract)
1. **Provision (human, outside ASTA):** the exchange account owner creates a *demo/testnet* key with READ + SPOT TRADE only (never withdrawal/transfer), for exactly one exchange environment. The value is never typed into chat, tickets, source, env files or the UI.
2. **Authorise:** a privilege configuration (venue, demo environment, permissions, connector id, validity window) is signed by two distinct reviewers (`verify_privilege_config`); the digest is recorded in the audit chain. (Replay protection/expiry fields: to be added in M3-B, F-06.)
3. **Store:** the key is handed directly to the approved vault backend (`store` refuses wrong class, forbidden permissions, empty); never written to the repository, database in plaintext, logs or browser storage.
4. **Grant and use:** the vault records a `grant(connector, identity, config digest)`; the connector, running only inside the protected runtime, takes a 60-second lease, checks it against its immutable `EnvironmentBinding`, uses it, and lets it expire. Every lease and every refusal is logged.
5. **Monitor:** repeated denials, leases outside the expected pattern, proxy DENY spikes and any `50101`-type environment-mismatch response raise alerts (alerting not built: F-07).
6. **Rotate:** `store` on the same identity bumps the version; old material is replaced; schedule at least every 30 days (the contract's default TTL) and on any suspicion.
7. **Revoke/destroy:** `revoke` destroys the material and blocks further leases; the exchange-side key is deleted by the owner; testnet balances/orders are disposable. After testnet resets (Binance, DOCUMENTED monthly) credentials may need re-issue.
8. **Incident:** suspected exposure -> revoke, rotate, review the access log and proxy log, scan repository and artifacts, record in the incident register.

## 5. Demo/production environment-separation design
* Binance: separate hosts (`testnet.binance.vision`, `stream.testnet.binance.vision`, `ws-api.testnet.binance.vision`) -> host allowlisting is effective; production trading hosts (`api.binance.com`, `stream.binance.com`, ...) are not in any allowlist profile (negative-tested).
* OKX: demo and production share the REST host; environment is selected by the `x-simulated-trading: 1` header plus a demo-created key (DOCUMENTED excerpts, not runtime verified). Hostname controls cannot separate them, so the design layers: demo-only credential class and vault identity; an immutable binding that records the required header; no production binding or class exists; a mismatch error (`50101`) is a fatal alarm; the only OKX demo WebSocket host (`wspap.okx.com`) is allowlisted only in a future reviewed configuration, the production WS host is a different name; and the proxy cannot see headers - so this layer must be proven with real demo responses in M3-B (UNVERIFIED).
* Runtime selection is immutable per connector instance; URL overrides, host switching, redirects, unexpected response origins and credential-class mismatches are refused (tests).
* `Environment` has no `LIVE`; the M0 ExecutionGateway stays default DENY; execution privileges require a signed two-reviewer configuration.

## 6. Remaining attack paths (honest list)
1. Compromise of the Docker Desktop VM/kernel or a Docker misconfiguration by another project -> bypasses both boundaries (needs the dedicated VM, F-02).
2. Compromise of the gate container (holds `NET_ADMIN` in its namespace) -> can rewrite the rules; mitigated by hardening/read-only/no published ports, not eliminated.
3. Legacy host processes with Internet egress and no boundary (F-01) - credential-free by policy; any future change that gives them credentials would defeat the design.
4. DNS: a malicious *public* answer for an allowed name (F-09); the gate refuses non-public answers but cannot validate public ones.
5. Same-host OKX demo/production (F-04) and header-level confusion not visible to a CONNECT proxy.
6. In-process vault (F-05): a compromised connector can read its own leased secrets and memory; enumerating other identities is prevented only by grants/in-process checks.
7. Supply chain: base images by tag (F-13), dependency compromise (audits clean today).
8. Insider/operator misuse: mitigated by MFA/RBAC/audit but the signed-config reviewer set is undefined (F-06).
9. Duplicate/runaway orders once an order path exists (F-12).
10. Evidence tampering was possible via test runs overwriting screenshots and by untracked logs: fixed in R1 (frozen-evidence manifest, tracked logs).

## 7. M0-M2 safety regression evidence
Full Python suite (542 passed at the M3-A commit; the final R1 count is in `docs/evidence/M3A-acceptance.md`), 15 live-lock tests, 39/39 browser tests, `git diff fe8194c` over risk/core/accepted-evidence empty, paper sessions reconciled (0 discrepancies, checkpoints replayed), frozen-evidence verifier green.
