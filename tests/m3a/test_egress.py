"""M3A-1: egress boundary. Policy unit tests + runtime tests INSIDE the real protected container."""

import asyncio
import importlib.util
import ipaddress
import json
import shutil
import socket
import subprocess
from pathlib import Path

import httpx
import pytest
import respx

from asta.exchanges.common.http import PublicHttp

ROOT = Path(__file__).resolve().parents[2]
EGRESS = ROOT / "infra" / "egress"


def load_proxy():
    spec = importlib.util.spec_from_file_location("asta_egress_proxy", EGRESS / "proxy.py")
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


proxy = load_proxy()
ALLOW = proxy.load_allowlist(EGRESS / "allowlist.json", "runtime")


@pytest.mark.parametrize(
    "host,port,ok,why",
    [
        ("data-api.binance.vision", 443, True, "allowlisted"),
        ("DATA-API.BINANCE.VISION", 443, True, "allowlisted"),
        ("data-api.binance.vision.", 443, True, "allowlisted"),
        ("data-api.binance.vision", 80, False, "port-not-allowlisted"),
        ("api.binance.com", 443, False, "host-not-allowlisted"),
        ("www.okx.com", 8443, False, "port-not-allowlisted"),
        ("wspap.okx.com", 8443, False, "host-not-allowlisted"),
        ("testnet.binance.vision", 443, False, "host-not-allowlisted"),  # runtime profile only
        ("1.1.1.1", 443, False, "ip-literal-denied"),
        ("127.0.0.1", 5432, False, "ip-literal-denied"),
        ("169.254.169.254", 80, False, "ip-literal-denied"),
        ("::1", 443, False, "ip-literal-denied"),
        ("2606:4700:4700::1111", 443, False, "ip-literal-denied"),
        ("data-api.binance.vision@evil.example", 443, False, "malformed-host"),
        ("localhost", 443, False, "malformed-host"),
        ("data-api.binance.vision:443", 443, False, "malformed-host"),
        ("evil.example", 443, False, "host-not-allowlisted"),
    ],
)
def test_target_policy(host, port, ok, why):
    assert proxy.check_target(host, port, ALLOW) == (ok, why)


@pytest.mark.parametrize(
    "ip,public",
    [
        ("52.199.187.27", True),
        ("127.0.0.1", False),
        ("10.0.0.5", False),
        ("172.16.1.1", False),
        ("192.168.1.1", False),
        ("169.254.169.254", False),
        ("100.64.0.1", False),
        ("100.100.100.200", False),
        ("0.0.0.0", False),  # noqa: S104
        ("224.0.0.1", False),
        ("240.0.0.1", False),
        ("198.18.0.1", False),
        ("::1", False),
        ("2606:4700:4700::1111", False),  # IPv6 refused wholesale
    ],
)
def test_public_address_classification(ip, public):
    assert proxy.is_public_address(ipaddress.ip_address(ip)) is public


def _fake_resolver(monkeypatch, answers):
    async def fake(self, host, port, **kw):  # noqa: ARG001
        return [
            (socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 0))
            for a in answers
        ]

    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", fake)


def test_dns_rebinding_and_sinkhole_are_refused(monkeypatch):
    for answers in (["127.0.0.1"], ["52.1.1.1", "169.254.169.254"], ["10.1.2.3"], ["::1"]):
        _fake_resolver(monkeypatch, answers)
        ip, why = asyncio.run(proxy.resolve_public_ipv4("data-api.binance.vision"))
        assert ip is None, (answers, why)


def test_a_public_answer_is_pinned_and_ipv6_answers_are_ignored(monkeypatch):
    _fake_resolver(monkeypatch, ["2606:4700::1", "52.199.187.27"])
    ip, why = asyncio.run(proxy.resolve_public_ipv4("data-api.binance.vision"))
    assert (ip, why) == ("52.199.187.27", "ok")


def test_allowlist_contains_only_named_public_market_data_hosts():
    data = json.loads((EGRESS / "allowlist.json").read_text(encoding="utf-8"))
    banned = ("api.binance.com", "api.okx.com", "fapi", "dapi", "sapi", "wspap", "ws-api", "trade")
    for profile, entries in data["profiles"].items():
        for e in entries:
            host = e["host"]
            with pytest.raises(ValueError):
                ipaddress.ip_address(host)  # never an IP
            assert not any(b in host for b in banned), (profile, host)
            assert all(p in (443, 9443) for p in e["ports"]), (profile, host)
    assert {e["host"] for e in data["profiles"]["runtime"]} <= {
        "data-api.binance.vision",
        "data-stream.binance.vision",
        "www.okx.com",
        "ws.okx.com",
    }
    # the testnet hosts exist only in the explicit probe profile
    assert "testnet.binance.vision" not in {e["host"] for e in data["profiles"]["runtime"]}


# ---------------------------------------------------------------- application-level redirect guard
@respx.mock
def test_public_http_never_follows_a_redirect_to_another_host():
    respx.get("https://data-api.binance.vision/api/v3/time").mock(
        return_value=httpx.Response(302, headers={"location": "https://evil.example/x"})
    )
    evil = respx.get("https://evil.example/x").mock(return_value=httpx.Response(200, json={}))

    async def run():
        c = PublicHttp("https://data-api.binance.vision")
        try:
            await c.get("/api/v3/time")
        finally:
            await c.aclose()

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(run())
    assert not evil.called


# ---------------------------------------------------------------- compose posture (static)
def _docker_ok() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=30).returncode == 0
    except Exception:  # noqa: BLE001
        return False


needs_docker = pytest.mark.skipif(not _docker_ok(), reason="Docker not available")


@needs_docker
def test_compose_hardening_is_declared():
    r = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(EGRESS / "docker-compose.egress.yml"),
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    cfg = json.loads(r.stdout)
    assert cfg["networks"]["asta_protected"]["internal"] is True
    assert not cfg["networks"]["asta_protected"].get("enable_ipv6")
    assert not cfg["networks"]["asta_egress"].get("enable_ipv6")
    for name, svc in cfg["services"].items():
        assert not svc.get("privileged"), name
        assert svc.get("network_mode") in (None, "bridge"), name
        assert svc["read_only"] is True, name
        assert svc["cap_drop"] == ["ALL"], name
        assert "no-new-privileges:true" in svc["security_opt"], name
        assert not svc.get("ports"), f"{name}: no host ports may be published"
        for v in svc.get("volumes", []):
            assert "docker.sock" not in v["source"], name
            assert v["read_only"] is True, (name, v)
    assert set(cfg["services"]["protected-probe"]["networks"]) == {"asta_protected"}
    assert set(cfg["services"]["egress-proxy"]["networks"]) == {"asta_protected", "asta_egress"}


def _container_running(name: str) -> bool:
    r = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", name], capture_output=True, text=True
    )
    return r.stdout.strip() == "true"


@needs_docker
def test_negative_tests_pass_from_inside_the_protected_container():
    if not (_container_running("asta-protected-probe") and _container_running("asta-egress-proxy")):
        pytest.skip("egress boundary not running (see docs/m3a/EGRESS-ENFORCEMENT.md)")
    r = subprocess.run(
        ["docker", "exec", "asta-protected-probe", "python", "/app/negative_tests.py"],
        capture_output=True,
        text=True,
        timeout=600,
        env={**__import__("os").environ, "MSYS_NO_PATHCONV": "1"},
    )
    out = json.loads(r.stdout)
    failed = [x for x in out["results"] if not x["pass"]]
    assert out["total"] >= 70 and not failed, failed
