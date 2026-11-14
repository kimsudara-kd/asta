# Binance Spot Testnet — vault readiness and approval-ready activation plan (M3-B-FINAL phase 6)

**No real credential was generated, imported or used. No authenticated order path is enabled. `ExecutionGateway` EXTERNAL stays DENY.**

## 1. Reconfirmed requirements (official `binance-spot-api-docs`, fetched 2026-11-14)
Key types supported: Ed25519, HMAC, RSA (2048/4096). "We recommend to use Ed25519 API keys"; "HMAC keys are deprecated". The signature sample is base64 (`==`
padding); the page does not state the encoding in words → the connector must be validated against the live testnet with a **public** (unsigned) check first and a
synthetic round-trip as implemented in the staged signer. The page does not describe Spot Testnet key registration: testnet keys are issued on the testnet
web page by an owner login; this is a step the owner performs (section 4), unverified by me.
Consequence unchanged from `VAULT-FEASIBILITY.md`: **Ed25519 is the only scheme where the private key can stay non-exportable end to end** (OpenBao transit,
`exportable=false`, public key registered at the exchange). OKX authenticated integration stays deferred.

## 2. Implemented and tested offline (this milestone)
`proposals/binance_testnet_signer/signer.py` (staged outside `src/` because the live-lock test bans signing vocabulary in `src/`; see its README), `tests/security/test_synthetic_signer.py` (18 tests):
* `Signer` protocol = `sign` + `public_key_b64` only (no key accessor); `SyntheticEd25519Signer` generates a random in-memory key, refuses pickling, logs only a key fingerprint and byte count.
* `build_signed_request`: **only** `https://testnet.binance.vision` (production, api1, futures, look-alike and plaintext hosts raise `ForbiddenEndpoint`); an allow-list of spot paths; withdraw/transfer/margin/futures/loan paths and parameters refused; `recvWindow` ≤ 5 s.
* `mock_endpoint_verify`: isolated mock exchange: signature, timestamp window, replay, host.
* Signer outage raises `SignerUnavailable`; there is no local fallback signer. Signature/key/query never in `repr`, audit or pickles. The module imports no network/OS module (AST test).
* Earlier (M3A/R5): vault policy (DEMO classes only, spot permissions only, grant-gated leases, hash-chained access log, redaction registry), recovery rehearsal 11/11.

## 3. Demo-only connector authorization boundary (to be reviewed, not enabled)
1. A new `EXCHANGE_DEMO` gateway capability, **off by default**, enabled only by a signed, two-reviewer configuration naming `venue=BINANCE_TESTNET`; production hosts are not representable (signer + `Environment` + egress allow-list `testnet.binance.vision` only).
2. The Dispatcher keeps refusing raw ports; the only external port is a `GatedExchange` wrapping the connector, behind `InternalGate` + pre-trade limits + kill switch + heartbeat.
3. Controls that need *replacement* before enabling (not changed now): `core/order_gate.py` EXTERNAL=DENY constant and the live-lock tests. Proposed replacement: the constant becomes `DENY unless capability token verified`, with new tests (a) default still denies, (b) a token for production venues is rejected, (c) token expiry, (d) AI/strategy principals can never hold it. The current live-lock tests stay unchanged and keep passing; new tests are additive. Requires independent review before merge.
4. Network: the credential VM (or signer process) has no Internet; the connector reaches only the firewall VM proxy; the proxy allow-list contains the testnet hosts only after a signed two-reviewer change.

## 4. Least-privilege exchange key provisioning (owner steps, later, after separate authorization)
1. Log in to the Spot **Testnet** (virtual funds) with an account used only for this. 2. Generate the key pair **inside OpenBao** (`transit/keys/binance-testnet-ed25519`, `exportable=false`); export the *public* key only.
3. Register the public key as an Ed25519 API key, permissions: **read + spot trading only; no withdrawal, no transfer, no margin/futures/loan**; restrict to the egress IP if the testnet supports it.
4. Record the key id (a label, not a secret) in the vault KV; never in git, env files or chat. 5. OpenBao policy: `update` on `transit/sign/binance-testnet-ed25519` and `transit/verify/...`, `read` on the public key; one AppRole for the connector; no human or AI role may sign. 6. Rotate by creating v2 and revoking v1 at the exchange.

## 5. External backup-key replacement ceremony (prepared)
The synthetic rehearsal used a throw-away age recipient. Real ceremony (owner + witness, offline): generate the age identity on an air-gapped machine or hardware token; print/engrave the secret on paper in two sealed envelopes (two locations); put only the *recipient* (public) key at `/etc/asta/backup-recipient.pub`;
restore-test with a **synthetic** dump decrypted using the envelope before the first real backup; log the ceremony (date, witness, fingerprint of the recipient key) in the evidence folder; destroy the working copy. OpenBao unseal shares (Shamir 3-of-5) follow the same envelope discipline and are **not** the backup key.

## 6. Activation preconditions (all required; none met today)
Independent review complete (F-01/F-02/F-03/H-1/H-3 closed), security host provisioned and verified, credential VM provisioned, §3 controls reviewed and merged, owner's separate written authorization for (a) key generation and (b) enabling the demo capability. Then: public-endpoint connectivity test → signed `order/test` (validates without placing) → one minimum limit order far from market → cancel → reconcile with the internal ledger → kill-switch drill, each recorded.
