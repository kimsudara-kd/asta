"""M3A-R2 B: the Linux security-host design artifacts (infra/security-host/) are checked statically
and, where Docker is available, with `nft -c` (syntax/semantics check, loads nothing)."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SH = ROOT / "infra" / "security-host"
FW = SH / "firewall-vm"
RT = SH / "runtime-vm"


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def unit(p: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for ln in read(p).splitlines():
        if "=" in ln and not ln.startswith(("#", "[")):
            k, v = ln.split("=", 1)
            out.setdefault(k.strip(), []).append(v.strip())
    return out


def test_firewall_vm_rules_are_default_deny_and_never_route():
    t = read(FW / "nftables.conf")
    assert t.count("policy drop;") == 3
    fwd = t.split("chain forward")[1].split("chain output")[0]
    assert "accept" not in fwd  # the firewall VM never forwards
    out = t.split("chain output")[1]
    assert "ip daddr @allowed_dst tcp dport { 443, 9443 } accept" in out
    assert "udp dport { " not in out and "ip6" not in t  # IPv6 only ever hits the drop policy
    # DNS/NTP egress only for root; the proxy user has none
    for line in out.splitlines():
        if "dport 53" in line or "dport 123" in line:
            assert "meta skuid 0" in line, line
    assert "tcp dport 3128 accept" in t and "$RUNTIME_IP" in t


def test_runtime_vm_rules_reach_only_the_proxy_and_have_no_route_or_dns():
    t = read(RT / "nftables.conf")
    assert t.count("policy drop;") == 3
    out = t.split("chain output")[1]
    accepts = [ln.strip() for ln in out.splitlines() if ln.strip().endswith("accept")]
    assert accepts == [
        "oif lo accept",
        "ct state established,related accept",
        'oifname "prot0" ip daddr $FIREWALL_IP tcp dport 3128 accept',
    ]
    net = read(RT / "netplan-50-asta.yaml")
    body = "\n".join(ln for ln in net.splitlines() if not ln.strip().startswith("#"))
    assert "routes:" not in body and "nameservers:" not in body and "gateway" not in body
    assert "dhcp4: false" in body and "accept-ra: false" in body


def test_firewall_vm_netplan_has_a_single_uplink_and_pinned_dns():
    body = read(FW / "netplan-50-asta.yaml")
    assert body.count("up0:") == 1 and "use-dns: false" in body and "accept-ra: false" in body


@pytest.mark.parametrize(
    "path",
    [
        FW / "asta-egress-proxy.service",
        FW / "asta-allowlist-refresh.service",
        RT / "asta-vault.service",
        RT / "asta-connector.service",
    ],
)
def test_systemd_units_are_hardened(path):
    u = unit(path)
    for key in (
        "NoNewPrivileges",
        "ProtectSystem",
        "PrivateTmp",
        "PrivateDevices",
        "RestrictNamespaces",
        "LockPersonality",
        "MemoryDenyWriteExecute",
        "SystemCallArchitectures",
    ):
        assert key in u, (path.name, key)
    assert u["NoNewPrivileges"] == ["yes"] and u["ProtectSystem"] == ["strict"]
    assert not any(v for v in u.get("AmbientCapabilities", [])) or path.name.endswith(
        "refresh.service"
    )


def test_vault_has_no_network_and_only_the_connector_group_can_reach_its_socket():
    v = unit(RT / "asta-vault.service")
    assert v["PrivateNetwork"] == ["yes"] and v["RestrictAddressFamilies"] == ["AF_UNIX"]
    assert v["User"] == ["asta-vault"] and v["CapabilityBoundingSet"] == [""]
    assert v["StateDirectoryMode"] == ["0700"] and v["LimitCORE"] == ["0"]
    s = unit(RT / "asta-vault.socket")
    assert s["SocketMode"] == ["0660"] and s["SocketGroup"] == ["asta-connector"]
    c = unit(RT / "asta-connector.service")
    assert c["IPAddressDeny"] == ["any"]
    assert set(c["IPAddressAllow"]) == {"10.77.0.1/32", "127.0.0.1/32"}
    assert c["Group"] == ["asta-connector"] and c["User"] != v["User"]


def test_proxy_unit_resolves_from_the_hosts_file_and_has_no_capabilities():
    u = unit(FW / "asta-egress-proxy.service")
    assert u["DynamicUser"] == ["yes"] and u["CapabilityBoundingSet"] == [""]
    assert "/etc/asta-egress/hosts:/etc/hosts" in u["BindReadOnlyPaths"][0]
    assert "10.77.0.1" in "".join(u["Environment"])


def test_refresh_script_only_admits_public_unicast_ipv4():
    s = read(FW / "asta-allowlist-refresh.sh")
    for private in ("127.*", "10.*", "169.254.*", "192.168.*", "172.1[6-9].*", "100.6[4-9].*"):
        assert private in s, private
    assert "getent ahostsv4" in s and "nft add element inet asta_fw allowed_dst" in s
    assert re.search(r"^set -eu", s, re.M)


def _docker_ok() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=30).returncode == 0
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _docker_ok(), reason="Docker not available")
@pytest.mark.parametrize("conf", ["firewall-vm", "runtime-vm"])
def test_nftables_files_pass_nft_check_only(conf):
    r = subprocess.run(  # noqa: S603
        [
            "docker",
            "run",
            "--rm",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "NET_ADMIN",
            "-v",
            f"{SH / conf}:/cfg:ro",
            "alpine:3.20",
            "sh",
            "-c",
            "apk add -q --no-cache nftables >/dev/null 2>&1 && nft -c -f /cfg/nftables.conf",
        ],
        capture_output=True,
        text=True,
        timeout=300,
        env={**__import__("os").environ, "MSYS_NO_PATHCONV": "1"},
    )
    assert r.returncode == 0, r.stderr
