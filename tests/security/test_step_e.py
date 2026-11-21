"""Step E tooling, offline: key generation, file signer, pinned HTTPS transport, and the probe against a local TLS mock."""

from __future__ import annotations

import datetime
import http.server
import json
import os
import pickle
import re
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.x509.oid import NameOID

D = Path(__file__).parents[2] / "proposals/binance_testnet_signer"
sys.path.insert(0, str(D))
import file_signer as fs  # type: ignore[import-not-found]  # noqa: E402
import https_transport as ht  # type: ignore[import-not-found]  # noqa: E402
import keygen as kg  # type: ignore[import-not-found]  # noqa: E402
import probe as pr  # type: ignore[import-not-found]  # noqa: E402
import signer as sg  # type: ignore[import-not-found]  # noqa: E402


# ------------------------------------------------------------------ keygen
def test_keygen_writes_a_0400_private_key_once_and_never_prints_it(tmp_path):
    p = tmp_path / "k.pem"
    r = subprocess.run(
        [sys.executable, str(D / "keygen.py"), "--private", str(p)], capture_output=True, text=True
    )  # noqa: S603
    assert r.returncode == 0 and oct(p.stat().st_mode & 0o777) == "0o400"
    body = p.read_text()
    assert "BEGIN PRIVATE KEY" in body and "BEGIN PUBLIC KEY" in r.stdout
    secret_b64 = "".join(body.splitlines()[1:-1])
    assert (
        secret_b64 not in r.stdout
        and secret_b64 not in r.stderr
        and "PRIVATE" not in r.stdout + r.stderr.replace("private key written", "")
    )
    again = subprocess.run(
        [sys.executable, str(D / "keygen.py"), "--private", str(p)], capture_output=True, text=True
    )  # noqa: S603
    assert (
        again.returncode == 1 and "REFUSING" in again.stderr and p.read_text() == body
    )  # never overwritten
    assert (
        subprocess.run([sys.executable, str(D / "keygen.py")], capture_output=True).returncode == 2
    )  # noqa: S603


# ------------------------------------------------------------------ file signer
def make_key(tmp_path: Path, name: str = "k.pem") -> tuple[Path, str]:
    p = tmp_path / name
    pub = kg.generate(str(p))
    return p, pub


def test_the_file_signer_signs_verifiably_and_exposes_no_key(tmp_path):
    p, pub_pem = make_key(tmp_path)
    s = fs.FileEd25519Signer(str(p))
    pub = serialization.load_pem_public_key(pub_pem.encode())
    assert isinstance(pub, Ed25519PublicKey)
    pub.verify(s.sign(b"hello"), b"hello")  # raises if wrong
    assert (
        s.synthetic is False
        and "PRIVATE" not in repr(s)
        and not any(
            "key" in n.lower()
            and not n.startswith("_FileEd")
            and n not in ("public_key_b64", "_fp")
            for n in vars(s)
        )
    )
    with pytest.raises(sg.SignerError):
        pickle.dumps(s)
    # works with the request builder and the mock verifier
    req = sg.build_signed_request(
        s,
        "POST",
        "/api/v3/order/test",
        {"symbol": "BTCUSDT", "side": "BUY", "type": "MARKET", "quoteOrderQty": "20"},
        1_800_000_000_000,
    )
    ok, why = sg.mock_endpoint_verify(s.public_key_b64(), req.url, 1_800_000_000_100)
    assert ok, why


def test_the_file_signer_refuses_unsafe_or_wrong_key_files(tmp_path):
    p, _ = make_key(tmp_path)
    for mode in (0o644, 0o440, 0o604, 0o666, 0o200 | 0o070):
        os.chmod(p, mode)
        with pytest.raises(sg.SignerError):
            fs.FileEd25519Signer(str(p))
    rsa_pem = rsa.generate_private_key(65537, 2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    ec_pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    for name, data in (("rsa", rsa_pem), ("ec", ec_pem), ("junk", b"not a key"), ("empty", b"")):
        f = tmp_path / name
        f.write_bytes(data)
        os.chmod(f, 0o400)
        with pytest.raises(sg.SignerError):
            fs.FileEd25519Signer(str(f))
    enc = tmp_path / "enc"
    enc.write_bytes(
        serialization.load_pem_private_key(
            p.read_bytes() if False else rsa_pem, None
        ).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(b"pw"),
        )
    )
    os.chmod(enc, 0o400)
    with pytest.raises(sg.SignerError):
        fs.FileEd25519Signer(str(enc))


# ------------------------------------------------------------------ a local TLS mock of the testnet
def self_signed(tmp: Path) -> tuple[str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(__import__("ipaddress").ip_address("127.0.0.1"))]), critical=False)
        .sign(key, hashes.SHA256())
    )  # fmt: skip
    c, k = tmp / "cert.pem", tmp / "key.pem"
    c.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    k.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return str(c), str(k)


class Mock:
    def __init__(self, tmp: Path, pub_b64: str | None = None) -> None:
        self.cert, key = self_signed(tmp)
        self.pub = pub_b64
        self.seen_signatures: set[str] = set()
        self.requests: list[tuple[str, str]] = []
        self.time_skew_ms = 0
        self.order_test: tuple[int, dict[str, Any]] = (200, {})
        self.account_balances = [
            {"asset": "USDT", "free": "10000.00", "locked": "0.0"},
            {"asset": "BTC", "free": "0.00000000", "locked": "0.0"},
        ]
        self.mode = ""  # "redirect" | "big" | "junk" | "slow"
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:
                pass

            def _send(
                self, status: int, body: bytes, headers: dict[str, str] | None = None
            ) -> None:
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _handle(self) -> None:
                path = self.path.split("?")[0]
                outer.requests.append((self.command, path))
                if outer.mode == "redirect":
                    return self._send(302, b"", {"Location": "https://evil.example/x"})
                if outer.mode == "big":
                    return self._send(200, b"x" * 100_000)
                if outer.mode == "junk":
                    return self._send(200, b"<html>not json</html>")
                if outer.mode == "slow":
                    time.sleep(2)
                if path == "/api/v3/time":
                    return self._send(
                        200,
                        json.dumps(
                            {"serverTime": int(time.time() * 1000) + outer.time_skew_ms}
                        ).encode(),
                    )
                ok, why = sg.mock_endpoint_verify(
                    outer.pub or "",
                    sg.TESTNET_BASE + self.path,
                    int(time.time() * 1000) + outer.time_skew_ms,
                    outer.seen_signatures,
                )
                if "X-MBX-APIKEY" not in self.headers:
                    return self._send(
                        401, json.dumps({"code": -2014, "msg": "API-key format invalid."}).encode()
                    )
                if not ok:
                    code = -1021 if why == "OUTSIDE_RECV_WINDOW" else -1022
                    return self._send(400, json.dumps({"code": code, "msg": why}).encode())
                if path == "/api/v3/order/test":
                    st, b = outer.order_test
                    return self._send(st, json.dumps(b).encode())
                if path == "/api/v3/account":
                    return self._send(
                        200,
                        json.dumps({"canTrade": True, "balances": outer.account_balances}).encode(),
                    )
                self._send(404, b"{}")

            do_GET = do_POST = do_DELETE = _handle  # noqa: N815

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self.cert, key)
        self.srv.socket = ctx.wrap_socket(self.srv.socket, server_side=True)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"https://127.0.0.1:{self.srv.server_port}"
        self.ctx = ssl.create_default_context(cafile=self.cert)

    def stop(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def mock(tmp_path):
    m = Mock(tmp_path)
    yield m
    m.stop()


# ------------------------------------------------------------------ transport
def test_the_transport_talks_https_to_the_pinned_host_and_refuses_everything_else(mock):
    t = ht.HttpsTransport(mock.ctx, 3.0, test_base=mock.base)
    st, body = t.public_get("/api/v3/time")
    assert st == 200 and "serverTime" in body
    with pytest.raises(ValueError):
        t.public_get("/api/v3/order")  # not a public path
    for bad in (
        "https://api.binance.com/api/v3/time",
        "http://testnet.binance.vision/api/v3/time",
        "https://testnet.binance.vision:8443/x",
        "https://testnet.binance.vision.evil.example/x",
    ):
        with pytest.raises(ValueError):
            t._target(bad)
    with pytest.raises(ValueError):
        ht.HttpsTransport(
            mock.ctx, 3.0, test_base="https://example.com"
        )  # a test base must be loopback
    with pytest.raises(ValueError):
        ht.HttpsTransport(mock.ctx, 3.0, test_base="http://127.0.0.1:1")
    insecure = ssl.create_default_context()
    insecure.check_hostname = False
    insecure.verify_mode = ssl.CERT_NONE
    with pytest.raises(ValueError):
        ht.HttpsTransport(insecure)  # verification can not be switched off


def test_an_untrusted_certificate_redirects_big_junk_slow_and_proxies_are_all_handled_safely(
    mock, monkeypatch
):
    with pytest.raises(OSError):
        ht.HttpsTransport(ssl.create_default_context(), 3.0, test_base=mock.base).public_get(
            "/api/v3/time"
        )  # self-signed: untrusted
    t = ht.HttpsTransport(mock.ctx, 0.5, test_base=mock.base)
    mock.mode = "redirect"
    before = len(mock.requests)
    st, _ = t.public_get("/api/v3/time")
    assert st == 302 and len(mock.requests) == before + 1  # NOT followed
    mock.mode = "big"
    with pytest.raises(OSError, match="too large"):
        t.public_get("/api/v3/time")
    mock.mode = "junk"
    with pytest.raises(OSError, match="not JSON"):
        t.public_get("/api/v3/time")
    mock.mode = "slow"
    with pytest.raises(
        OSError
    ):  # timeouts surface as OSError (TimeoutError): the connector maps them to UNKNOWN
        t.public_get("/api/v3/time")
    mock.mode = ""
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:9")
    assert t.public_get("/api/v3/time")[0] == 200  # a proxy in the environment is ignored


def test_errors_never_carry_the_url(mock):
    t = ht.HttpsTransport(mock.ctx, 0.3, test_base="https://127.0.0.1:9")  # nothing listens
    with pytest.raises(OSError) as e:
        t.public_get("/api/v3/time")
    assert "127.0.0.1" not in str(e.value) and "testnet" not in str(e.value)


# ------------------------------------------------------------------ the probe
def probe_cfg(tmp: Path, mock: Mock, **over: Any) -> tuple[dict[str, Any], Any]:
    pem, _ = make_key(tmp, "probe.pem")
    s = fs.FileEd25519Signer(str(pem))
    mock.pub = s.public_key_b64()
    api = tmp / "api.key"
    api.write_text("TESTNET-API-KEY-ID-0123456789\n")
    os.chmod(api, 0o400)
    cfg = {"stage": "probe", "private_key_file": str(pem), "api_key_file": str(api), **over}
    return cfg, ht.HttpsTransport(mock.ctx, 3.0, test_base=mock.base)


def test_the_probe_runs_the_three_requests_and_nothing_else_and_prints_no_secret(tmp_path, mock):
    cfg, t = probe_cfg(tmp_path, mock)
    out, rc = pr.run(cfg, t)
    assert rc == 0 and out["order_test"] == {
        "http": 200,
        "code": None,
        "verdict": "SIGNATURE_AND_ORDER_VALID",
    }
    assert out["account"]["can_trade"] is True and out["account"]["nonzero_assets"] == [
        {"asset": "USDT", "free": "10000.00"}
    ]
    assert [r for r in mock.requests] == [
        ("GET", "/api/v3/time"),
        ("POST", "/api/v3/order/test"),
        ("GET", "/api/v3/account"),
    ]
    blob = json.dumps(out)
    for secret in ("TESTNET-API-KEY-ID", "signature", "BEGIN", "http", "testnet.binance"):
        assert secret not in blob.replace('"http"', "")  # the only 'http' is the status field name


def test_the_probe_reports_a_rejected_signature_and_a_bad_clock_without_signing_further(
    tmp_path, mock
):
    cfg, t = probe_cfg(tmp_path, mock)
    mock.order_test = (
        400,
        {"code": -2015, "msg": "Invalid API-key, IP, or permissions for action."},
    )
    out, rc = pr.run(cfg, t)
    assert rc == 1 and out["order_test"]["verdict"] == "API_KEY_OR_PERMISSIONS_REJECTED"
    mock.order_test = (400, {"code": -1013, "msg": "Filter failure: NOTIONAL"})
    assert (
        pr.run(cfg, t)[0]["order_test"]["verdict"] == "SIGNATURE_OK_ORDER_REFUSED_BY_VENUE_FILTERS"
    )
    mock.requests.clear()
    mock.time_skew_ms = 30_000
    out, rc = pr.run(cfg, t)
    assert (
        rc == 3 and "clock offset" in out["refused"] and mock.requests == [("GET", "/api/v3/time")]
    )  # nothing was signed


def test_the_probe_can_never_place_an_order(tmp_path, mock):
    text = (D / "probe.py").read_text()
    assert not re.search(r'"/api/v3/order(?!/test)', text) and "newOrderRespType" not in text
    cfg, t = probe_cfg(tmp_path, mock)
    with pytest.raises(pr.ProbeConfigError):
        pr._signed(
            fs.FileEd25519Signer(cfg["private_key_file"]),
            t,
            "KEY",
            "POST",
            "/api/v3/order",
            {"symbol": "BTCUSDT"},
            1,
        )
    with pytest.raises(pr.ProbeConfigError):
        pr._signed(
            fs.FileEd25519Signer(cfg["private_key_file"]),
            t,
            "KEY",
            "DELETE",
            "/api/v3/openOrders",
            {"symbol": "BTCUSDT"},
            1,
        )
    for stage in ("testnet", "live", "order", ""):
        p = tmp_path / "c.json"
        p.write_text(json.dumps({**cfg, "stage": stage}))
        with pytest.raises(pr.ProbeConfigError):
            pr.load_config(str(p))
    pr.run(cfg, t)
    assert ("POST", "/api/v3/order") not in mock.requests


def test_the_probe_refuses_unsafe_secret_files_and_prints_only_a_refusal(tmp_path, mock):
    cfg, t = probe_cfg(tmp_path, mock)
    os.chmod(cfg["api_key_file"], 0o644)
    with pytest.raises(pr.ProbeConfigError):
        pr.run(cfg, t)
    os.chmod(cfg["api_key_file"], 0o400)
    p = tmp_path / "c.json"
    p.write_text(json.dumps({**cfg, "private_key_file": str(tmp_path / "nope.pem")}))
    r = subprocess.run(
        [sys.executable, str(D / "probe.py"), "--config", str(p)], capture_output=True, text=True
    )  # noqa: S603
    assert r.returncode in (1, 2) and "TESTNET-API-KEY" not in r.stdout + r.stderr


def test_the_credential_side_network_code_is_only_in_the_pinned_transport():
    for p in D.glob("*.py"):
        text = p.read_text()
        uses_net = any(
            w in text for w in ("urllib.request", "import socket", "import ssl", "import http")
        )
        if p.name in ("https_transport.py", "credential_service.py", "probe.py"):
            continue  # transport: pinned HTTPS; service: AF_UNIX server; probe: ssl context only
        assert not uses_net, p.name
    assert "AF_INET" not in (D / "credential_service.py").read_text()
