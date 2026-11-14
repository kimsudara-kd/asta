# STAGED PROPOSAL — not part of the product, not importable as `asta.*`

`signer.py` is the offline, synthetic-only Binance Spot Testnet signer + mock exchange verifier (M3-B-FINAL phase 6).
It is deliberately **outside `src/`**: `tests/live_lock/test_import_boundaries.py::test_no_signing_or_credentials_anywhere` forbids signing/credential
vocabulary anywhere under `src/`, and that protection must not be weakened. Moving this module into `src/asta/security/` is a **protection replacement** that needs:
1. an independently reviewed change to that test replacing the blanket ban with an allow-list naming exactly this file (and only this file),
2. new additive tests that (a) any other file under `src/` using signing vocabulary still fails, (b) the module has no network/OS import, (c) production hosts are unrepresentable (both already in `tests/security/test_synthetic_signer.py`),
3. the gateway capability design in `docs/m3b/BINANCE-TESTNET-ACTIVATION-PLAN.md` §3.
Until then the tests load it by file path. Nothing here is wired into any runtime path.
