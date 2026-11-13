# Credential isolation and demo environment binding (M3A-2 / Workstream C)

**No real exchange credential exists in this repository, was created, requested or used.** Everything below is contract + mock harness with random synthetic secrets (`SYNTH-...`, never valid at any exchange). The real backend, key custody and rotation operations are a later, independently reviewed decision (M3-B or later).

Code: `src/asta/security/` (`redaction.py`, `vault.py`, `binding.py`); tests `tests/m3a/test_security.py` (60 tests). Import boundary: nothing outside `asta.security` imports it (test `test_nothing_outside_the_security_package_imports_it`), and it imports only `asta.core`/`asta.audit` and no network library.

## 1. Vault contract
| Requirement | Implementation / evidence |
|---|---|
| Separate vault identities for Binance and OKX | `VaultIdentity.BINANCE_SPOT_TESTNET`, `OKX_DEMO`; identity-to-class map; mismatch refused (`test_empty_permissions_and_class_mismatch_are_refused`) |
| Demo-only classification | `CredentialClass` has only `DEMO_BINANCE_SPOT_TESTNET` and `DEMO_OKX`; there is no production class (`test_there_is_no_production_credential_class`) |
| Least privilege / permissions constrained | only `READ` and `SPOT_TRADE`; WITHDRAW, TRANSFER, MARGIN, FUTURES, BORROW, SUBACCOUNT, ACCOUNT_MANAGEMENT refused at `store` (7 parametrised tests); empty permission set refused |
| Restricted retrieval | only a `CONNECTOR` principal that holds an explicit **grant** for that identity can `lease`; STRATEGY, AI_MODULE, ANALYTICS, OPERATOR, UI are always denied (5 tests); grant needs a verified configuration digest |
| Audit of access | every store/rotate/revoke/grant/lease and every refusal appended to a hash-chained log (`asta.audit.chain`), tamper-evident (`test_access_log_is_hash_chained_tamper_evident_and_secret_free`), no secret material in entries |
| Encryption at rest | `EncryptedFileBackend`: authenticated encryption (Fernet), per-record random IV; test shows plaintext not in file bytes, wrong key and bit-flip tampering refused |
| Secret masking | `SecretValue` masks in `str/repr/format/%`, refuses pickle/copy/json/mutation/equality; `RedactingFilter` and `scrub_exception` scrub registered secrets and generic `secret=...` shapes from log lines, exception text and crash-report tracebacks (tests) |
| Rotation / revocation | `store` on an existing identity bumps the version; `revoke` destroys the material and refuses later leases; expiry refuses leases (3 tests) |
| Leases | short-lived (60 s default) and bounded by credential expiry |
| No credentials in source, Git history, frontend bundles, browser storage, ordinary env files, crash reports | `settings.assert_no_exchange_credentials` (unchanged, refuses exchange credential env names); repository tests: no `.env*` files, no key-shaped literals in `src/`, `apps/web/src`, `scripts/`, `infra/`; frontend contains no credential/secret-storage references; gitleaks over history and the custom `secret_scan.py` (results in the acceptance record) |

## 2. Credential-handling policy (normative for M3-B)
1. Strategies, AI modules, analytics and the UI never receive raw credentials; there is no code path from them to the vault.
2. Only a separately authorized exchange-demo connector, running in the protected runtime, may lease a credential, and only after a grant verified against a reviewed, signed configuration.
3. Minimum permissions: READ and SPOT_TRADE on a *demo* credential. **No withdrawal, transfer, margin, futures or borrowing permission may exist**; a credential carrying them is refused at store time and the human issuing demo keys must create them without those permissions (UNVERIFIED that each exchange lets a demo key be created without them; to be checked in M3-B).
4. Credentials are never placed in environment variables, files in the repository, the database in plaintext, frontend state or logs.
5. Any detected exposure triggers revocation and rotation; the access log is the audit trail.

## 3. Demo environment binding (Workstream C)
`EnvironmentBinding` (frozen): one per future connector instance, fixed at construction.
* Only `EXCHANGE_DEMO` can be bound; `PAPER`/`BACKTEST` and any production notion are refused; `Environment` still has no `LIVE` member (live-lock test).
* URLs are built from **path only** on the bound host: absolute URLs, `//host`, backslashes, `@`, `#`, spaces, CR/LF and `..` are refused (9 parametrised cases x 2 venues) - no runtime host switching or URL override.
* Redirects: any 3xx raises (`reject_redirect`) - never followed; unexpected response origin host raises (`check_response`).
* Credential-class mismatch (an OKX demo credential on the Binance binding) raises.
* WebSocket hosts are an explicit tuple; the production OKX WS host is not in the OKX demo binding.
* Shared OKX host: the demo environment is selected by a **header** (`x-simulated-trading: 1`, DOCUMENTED, not runtime verified); the binding records it as `required_headers`. Because the host is shared, host allowlisting cannot protect against sending a production-keyed request, so M3-B must add: demo-only credentials, mandatory header, treatment of environment-mismatch error responses (OKX `50101`, DOCUMENTED excerpt) as a fatal alarm, and the unexpected-response-indicator check defined with real demo responses (**not implementable or verifiable in M3-A: UNVERIFIED**).
* Signed, reviewed configuration: `verify_privilege_config` requires >= 2 **distinct** valid Ed25519 reviewer signatures over the canonical configuration, the demo environment only, and permissions limited to READ/SPOT_TRADE; tampering, duplicate signer, unknown signer, forbidden content or a `LIVE` environment are refused (test `test_privilege_config_needs_two_distinct_valid_reviewer_signatures`). The reviewer public-key set and its custody are not defined yet (M3-B/security review item).

## 4. What is deliberately NOT built
Real backend (HSM/OS keystore/managed vault), key custody and rotation ops, request signing, any authenticated HTTP/WS client, connector runtime, demo order placement, response-indicator verification. Adding any of these is outside M3-A and, for the signing/authenticated parts, blocked by the live-lock tests (`hmac`, `passphrase`, `api_secret`, `OK-ACCESS`, `X-MBX-APIKEY` may not appear in `src/`; unchanged and unweakened).

## 5. Known gaps
* The vault is not yet backed by an access control outside the process; in-process callers can import it (mitigated by the import-boundary test and by there being no caller). The real design needs process/container separation (vault process or OS keystore) so that a compromised connector cannot enumerate other identities.
* `SecretValue` cannot prevent a process memory dump or debugger access; the scrubbing registry only knows secrets that passed through the vault in this process.
* Key custody for `EncryptedFileBackend` is the caller's problem (test keys are random per run).
