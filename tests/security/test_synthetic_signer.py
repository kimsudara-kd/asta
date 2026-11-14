"""M3-B-FINAL phase 6: synthetic-only Ed25519 signing against an isolated mock endpoint (offline)."""

from __future__ import annotations

import ast
import pickle
from pathlib import Path

import pytest

from asta.security import signer as sg

NOW = 1_800_000_000_000


def _req(s: sg.Signer, **kw):
    return sg.build_signed_request(
        s, "POST", "/api/v3/order", {"symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT"}, NOW, **kw
    )


def test_sign_and_mock_verify_roundtrip():
    s = sg.SyntheticEd25519Signer()
    r = _req(s)
    assert sg.mock_endpoint_verify(s.public_key_b64(), r.url, NOW + 100) == (True, "OK")


def test_tampering_wrong_key_expiry_and_replay_are_rejected():
    s, other = sg.SyntheticEd25519Signer(), sg.SyntheticEd25519Signer()
    r = _req(s)
    pk = s.public_key_b64()
    assert sg.mock_endpoint_verify(pk, r.url.replace("BUY", "SELL"), NOW) == (
        False,
        "BAD_SIGNATURE",
    )
    assert sg.mock_endpoint_verify(other.public_key_b64(), r.url, NOW)[1] == "BAD_SIGNATURE"
    assert sg.mock_endpoint_verify(pk, r.url, NOW + 60_000)[1] == "OUTSIDE_RECV_WINDOW"
    seen: set[str] = set()
    assert sg.mock_endpoint_verify(pk, r.url, NOW, seen)[0] is True
    assert sg.mock_endpoint_verify(pk, r.url, NOW, seen) == (False, "REPLAY")
    assert sg.mock_endpoint_verify(pk, r.url.replace("testnet.", ""), NOW)[1] == "WRONG_HOST"


@pytest.mark.parametrize(
    "base",
    [
        "https://api.binance.com",
        "https://api1.binance.com",
        "https://testnet.binance.vision.evil.test",
        "http://testnet.binance.vision",
        "https://fapi.binance.com",
    ],
)
def test_only_the_spot_testnet_host_can_be_signed_for(base):
    with pytest.raises(sg.ForbiddenEndpoint):
        _req(sg.SyntheticEd25519Signer(), base_url=base)


@pytest.mark.parametrize(
    "path",
    [
        "/sapi/v1/capital/withdraw/apply",
        "/sapi/v1/asset/transfer",
        "/sapi/v1/margin/order",
        "/fapi/v1/order",
        "/sapi/v1/loan/borrow",
        "/api/v3/userDataStream",
    ],
)
def test_withdraw_transfer_margin_futures_loan_paths_are_not_signable(path):
    with pytest.raises(sg.ForbiddenEndpoint):
        sg.build_signed_request(sg.SyntheticEd25519Signer(), "POST", path, {}, NOW)


def test_forbidden_parameters_and_unbounded_recv_window_are_refused():
    s = sg.SyntheticEd25519Signer()
    for p in ({"sideEffectType": "MARGIN_BUY"}, {"isIsolated": "TRUE"}, {"signature": "x"}):
        with pytest.raises(sg.ForbiddenEndpoint):
            sg.build_signed_request(s, "POST", "/api/v3/order", p, NOW)
    with pytest.raises(sg.SignerError):
        _req(s, recv_window_ms=60_000)


def test_signer_failure_fails_closed_with_no_local_fallback():
    s = sg.SyntheticEd25519Signer()
    s.available = False
    with pytest.raises(sg.SignerUnavailable):
        _req(s)


def test_nothing_secret_reaches_repr_audit_or_pickle():
    events: list[tuple[str, dict[str, str]]] = []
    s = sg.SyntheticEd25519Signer(on_event=lambda a, p: events.append((a, p)))
    r = _req(s)
    sig = r.url.rsplit("signature=", 1)[1]
    blob = repr(s) + repr(r) + repr(events)
    assert sig not in blob and "BTCUSDT" not in blob and "signature" not in blob
    assert events and events[0][0] == "signer.sign" and set(events[0][1]) == {"key", "bytes"}
    with pytest.raises(sg.SignerError):
        pickle.dumps(s)
    assert not any("private" in n.lower() or "export" in n.lower() for n in dir(sg.Signer))


def test_the_module_has_no_network_or_credential_loading_code():
    src = Path(sg.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = {
        a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    }
    imported |= {
        n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
    }
    assert not imported & {
        "socket",
        "http",
        "requests",
        "httpx",
        "urllib3",
        "os",
        "subprocess",
        "ssl",
    }
    assert (
        "urlopen" not in src
        and "load_pem_private_key" not in src
        and "from_private_bytes" not in src
    )
