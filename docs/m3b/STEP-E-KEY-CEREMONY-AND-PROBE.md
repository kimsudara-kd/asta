# Step E — testnet key ceremony and the first authenticated probe (you run it; the assistant never sees a secret)

**Scope:** Binance **Spot Testnet** (virtual funds). The probe makes exactly three requests: a public time check, a signed
`POST /api/v3/order/test` (the venue **validates** the order and **creates nothing**), and a read-only `GET /api/v3/account`.
It cannot place an order: `stage` must be `probe`, the signed paths are restricted to those two, and a test proves no
`POST /api/v3/order` is ever sent. The gateway, the constant and the order path are **not involved** and stay OFF.

**What was already verified (2026-11-21, no credentials):** one public `GET /api/v3/time` against the real testnet from this Mac: HTTP 200,
clock offset ≈ 158 ms (limit 1000 ms), TLS verified with the `certifi` bundle (this Mac's python.org Python has no CA bundle of its own).

## What you do (about 5 minutes)
1. **A private folder** (only you can read it):
   `mkdir -p ~/asta-private/testnet && chmod 700 ~/asta-private/testnet`
2. **Generate the key pair.** The private key is written to the file with mode 0400 and is **never printed**; the *public* key is printed:
   `cd ~/asta && .venv/bin/python proposals/binance_testnet_signer/keygen.py --private ~/asta-private/testnet/ed25519.pem`
   (it refuses to overwrite an existing key). Copy the public key block (`-----BEGIN PUBLIC KEY----- … -----END PUBLIC KEY-----`).
3. **Register the public key on the testnet site.** Open https://testnet.binance.vision/ and sign in (GitHub). On the API-key page choose the
   option to register your own **Ed25519 public key** (the exact button wording may differ from what I describe: look for "Ed25519"), paste the
   public key, give it a label like `asta-probe`. The site shows an **API key id**. (Testnet has no withdrawals and only virtual funds.)
4. **Save the API key id privately** (not in chat, not in git):
   `read -r K && printf %s "$K" > ~/asta-private/testnet/api.key && chmod 400 ~/asta-private/testnet/api.key` — paste the id, press Enter.
5. **Create the probe config** `~/asta-private/testnet/probe.json`:
   ```json
   {"stage": "probe",
    "private_key_file": "/Users/apple/asta-private/testnet/ed25519.pem",
    "api_key_file": "/Users/apple/asta-private/testnet/api.key",
    "probe_stamp_file": "/Users/apple/asta-private/testnet/probe.stamp",
    "symbol": "BTCUSDT", "quote": "20", "max_clock_offset_ms": 1000}
   ```
6. **Run the probe:**
   `cd ~/asta && .venv/bin/python proposals/binance_testnet_signer/probe.py --config ~/asta-private/testnet/probe.json`
   A pass also writes `probe.stamp` (valid 7 days, for exactly this key): step F's service refuses `testnet` mode without it. Without `probe_stamp_file` in the config no stamp is written.
7. **Send me the JSON it prints** — it contains no key, no signature and no URL. Then I can interpret it.

## How to read the result
| Output | Meaning | What to do |
|---|---|---|
| `order_test.verdict = SIGNATURE_AND_ORDER_VALID`, `account.http = 200`, exit 0 | the signature scheme, key registration, clock and TLS all work | step E passed |
| `SIGNATURE_REJECTED` (-1022) | the venue does not accept our signature (wrong public key registered, or encoding) | re-register the printed public key; I will check the encoding |
| `API_KEY_OR_PERMISSIONS_REJECTED` (-2015) | key id wrong, key lacks the permission, or an IP restriction is set | check the key id and permissions on the site |
| `CLOCK_OUTSIDE_RECV_WINDOW` (-1021) or exit 3 | this Mac's clock is off | fix the system clock (System Settings → Date & Time → set automatically) |
| `SIGNATURE_OK_ORDER_REFUSED_BY_VENUE_FILTERS` | signing works; the venue refused the *order shape* (e.g. below minimum notional) | fine for step E; raise `quote` and rerun |
| `failed: OSError` | network/TLS problem | tell me; nothing was signed past that point |

## Revoke / clean up at any time
Delete the key on the testnet site, then `rm ~/asta-private/testnet/ed25519.pem ~/asta-private/testnet/api.key`. A testnet key controls only virtual funds.

## What comes after a passing probe (each needs your separate approval)
* **E-2:** the service gets a `testnet` mode that uses `FileEd25519Signer` + `HttpsTransport` (the code is built and tested; the mode stays refused until you ask).
* **F:** tiny real testnet orders — needs: the no-VM service installed, host hardening, the reviewed constant flipped in a separate commit, an enablement by a listed releaser (≤ 24 h),
  caps confirmed (20 USDT/order, 200 USDT/day, BTC-USDT + ETH-USDT proposed), a re-review of the gateway path, and the off-host heartbeat live.
