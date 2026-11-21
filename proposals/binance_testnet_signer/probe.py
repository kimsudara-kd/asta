"""Step E probe: the FIRST authenticated contact with the Binance Spot Testnet, and it cannot place an order.

    python probe.py --config probe.json

It performs exactly three requests and nothing else:
  1. public  GET  /api/v3/time          (clock offset check; no credentials)
  2. signed  POST /api/v3/order/test    (the venue validates the signed order and CREATES NOTHING)
  3. signed  GET  /api/v3/account       (read-only balances of the virtual account)
Only ``"stage": "probe"`` is accepted; the signed paths are restricted to the two above here, in addition to the signer's own
allow-lists. The output never contains a URL, a signature, an API key or any key material.
Exit codes: 0 all good · 1 a request failed or was refused · 2 bad configuration · 3 clock offset too large.
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from file_signer import FileEd25519Signer  # noqa: E402
from https_transport import HttpsTransport  # noqa: E402
from signer import SignerError, build_signed_request  # noqa: E402

PROBE_PATHS = frozenset({"/api/v3/order/test", "/api/v3/account"})
REQUIRED = ("stage", "private_key_file", "api_key_file")


class ProbeConfigError(Exception):
    pass


def read_private_line(path: str) -> str:
    st = os.stat(path)
    if st.st_mode & 0o177 or st.st_uid not in (os.getuid(), 0):
        raise ProbeConfigError(f"{path}: must be private (0400/0600) and owned by this user")
    value = Path(path).read_text(encoding="ascii").strip()
    if not value or "\n" in value:
        raise ProbeConfigError(f"{path}: expected one line")
    return value


def write_stamp(path: str, fingerprint: str, ts: int, verdict: str) -> None:
    """Procedural guard, not a security boundary: the same user could forge it. It only enforces the ORDER E before F."""
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o400)
    try:
        os.write(
            fd, json.dumps({"fingerprint": fingerprint, "ts": ts, "verdict": verdict}).encode()
        )
    finally:
        os.close(fd)
    os.replace(tmp, path)


def read_stamp(path: str, fingerprint: str, now: float, max_age_s: int = 7 * 86400) -> None:
    """Raises ProbeConfigError unless a passing probe stamp for this key exists and is fresh."""
    try:
        s = json.loads(Path(path).read_text(encoding="ascii"))
    except (OSError, ValueError) as exc:
        raise ProbeConfigError("no passing probe stamp: run the step E probe first") from exc
    if s.get("verdict") != "SIGNATURE_AND_ORDER_VALID" or s.get("fingerprint") != fingerprint:
        raise ProbeConfigError("the probe stamp is not a pass for this key")
    if not 0 <= now - int(s.get("ts", 0)) <= max_age_s:
        raise ProbeConfigError("the probe stamp is stale: run the probe again")


def load_config(path: str) -> dict[str, Any]:
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    missing = [k for k in REQUIRED if k not in cfg]
    if missing:
        raise ProbeConfigError(f"missing config keys: {missing}")
    if cfg["stage"] != "probe":
        raise ProbeConfigError("only stage 'probe' exists: this tool can never place an order")
    return cfg  # type: ignore[no-any-return]


def _ssl_context(cfg: dict[str, Any]) -> ssl.SSLContext:
    cafile = cfg.get("ca_bundle")
    if cafile is None:
        try:
            import certifi

            cafile = certifi.where()
        except ImportError:
            cafile = None
    return ssl.create_default_context(cafile=cafile)


def _signed(
    signer: Any,
    transport: Any,
    key_id: str,
    method: str,
    path: str,
    params: dict[str, str],
    now_ms: int,
) -> tuple[int, dict[str, Any]]:
    if path not in PROBE_PATHS:
        raise ProbeConfigError("path not permitted in the probe")
    req = build_signed_request(signer, method, path, params, now_ms, api_key_id=key_id)
    return transport(req, 10.0)  # type: ignore[no-any-return]


def run(
    cfg: dict[str, Any], transport: HttpsTransport, clock: Any = time.time
) -> tuple[dict[str, Any], int]:
    signer = FileEd25519Signer(cfg["private_key_file"])
    key_id = read_private_line(cfg["api_key_file"])
    out: dict[str, Any] = {
        "stage": "probe",
        "public_key_fingerprint": repr(signer).split("=")[1].rstrip(">"),
    }
    t0 = clock()
    status, body = transport.public_get("/api/v3/time")
    if status != 200 or "serverTime" not in body:
        out["time"] = {"http": status}
        return out, 1
    offset = int(body["serverTime"]) - int((t0 + clock()) / 2 * 1000)
    out["clock_offset_ms"] = offset
    if abs(offset) > int(cfg.get("max_clock_offset_ms", 1000)):
        out["refused"] = "clock offset too large: fix the system clock before signing"
        return out, 3
    symbol = str(cfg.get("symbol", "BTCUSDT"))
    st, b = _signed(signer, transport, key_id, "POST", "/api/v3/order/test",
                    {"symbol": symbol, "side": "BUY", "type": "MARKET", "quoteOrderQty": str(cfg.get("quote", "20"))},
                    int(clock() * 1000))  # fmt: skip
    code = b.get("code")
    verdict = "SIGNATURE_AND_ORDER_VALID" if st == 200 else {
        -1022: "SIGNATURE_REJECTED", -2015: "API_KEY_OR_PERMISSIONS_REJECTED", -1021: "CLOCK_OUTSIDE_RECV_WINDOW",
        -1013: "SIGNATURE_OK_ORDER_REFUSED_BY_VENUE_FILTERS", -2010: "SIGNATURE_OK_ORDER_REFUSED_BY_VENUE",
    }.get(code if isinstance(code, int) else 0, "REFUSED")  # fmt: skip
    out["order_test"] = {"http": st, "code": code, "verdict": verdict}
    st2, b2 = _signed(signer, transport, key_id, "GET", "/api/v3/account", {}, int(clock() * 1000))
    acct: dict[str, Any] = {"http": st2}
    if st2 == 200:
        acct["can_trade"] = bool(b2.get("canTrade"))
        acct["nonzero_assets"] = [
            {"asset": str(x.get("asset")), "free": str(x.get("free"))}
            for x in b2.get("balances", [])
            if str(x.get("free", "0")).strip("0.") or str(x.get("locked", "0")).strip("0.")
        ][:20]
    else:
        acct["code"] = b2.get("code")
    out["account"] = acct
    rc = 0 if st == 200 and st2 == 200 else 1
    stamp = cfg.get("probe_stamp_file")
    if (
        rc == 0 and stamp
    ):  # step F's service refuses testnet mode without a fresh stamp for the SAME key
        write_stamp(stamp, out["public_key_fingerprint"], int(clock()), verdict)
    return out, rc


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] != "--config":
        print("usage: probe.py --config FILE", file=sys.stderr)
        return 2
    try:
        cfg = load_config(argv[2])
        transport = HttpsTransport(_ssl_context(cfg))
        out, rc = run(cfg, transport)
    except (ProbeConfigError, SignerError, OSError, ValueError) as exc:
        print(
            f"refusing: {type(exc).__name__}: {exc}"
            if isinstance(exc, (ProbeConfigError, SignerError))
            else f"failed: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 2 if isinstance(exc, (ProbeConfigError, SignerError, ValueError)) else 1
    print(json.dumps(out, indent=1))
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
