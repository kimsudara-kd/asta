"""Negative / positive egress tests, run INSIDE the protected container (stdlib only).

    docker exec asta-protected-probe python /app/negative_tests.py

Each test records what was attempted and the observed outcome. A test PASSES when a prohibited
destination is unreachable (fail closed) or when an approved public market-data destination works.
Nothing here uses credentials; no authenticated request is made; no order endpoint is called.
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import sys

PROXY = ("asta-egress-proxy", 3128)
RESULTS: list[dict[str, object]] = []


def record(test: str, desc: str, expected: str, observed: str, ok: bool) -> None:
    RESULTS.append(
        {"test": test, "desc": desc, "expected": expected, "observed": observed, "pass": ok}
    )


def direct(host: str, port: int, timeout: float = 4.0, family: int = socket.AF_UNSPEC) -> str:
    """Try a DIRECT connection (bypassing the proxy). Returns 'CONNECTED' or an error name."""
    try:
        infos = socket.getaddrinfo(host, port, family, socket.SOCK_STREAM)
    except Exception as exc:  # noqa: BLE001
        return f"no-resolve:{type(exc).__name__}"
    try:
        s = socket.socket(infos[0][0], socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(infos[0][4])
        s.close()
        return "CONNECTED"
    except Exception as exc:  # noqa: BLE001
        return f"blocked:{type(exc).__name__}"


def raw_proxy(request: bytes) -> str:
    try:
        s = socket.create_connection(PROXY, 5)
        s.settimeout(8)
        s.sendall(request)
        data = s.recv(200)
        s.close()
        return data.split(b"\r\n", 1)[0].decode("latin-1") or "EMPTY"
    except Exception as exc:  # noqa: BLE001
        return f"error:{type(exc).__name__}"


def connect_via_proxy(target: str) -> str:
    return raw_proxy(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())


def denied(status: str) -> bool:
    return status.startswith(("HTTP/1.1 403", "HTTP/1.1 405", "HTTP/1.1 400", "HTTP/1.1 502"))


def expect_denied_via_proxy(test: str, desc: str, target: str) -> None:
    st = connect_via_proxy(target)
    record(test, f"{desc}: CONNECT {target}", "proxy refuses", st, denied(st))


def expect_unreachable_direct(test: str, desc: str, host: str, port: int, fam: int = 0) -> None:
    r = direct(host, port, family=fam)
    record(test, f"{desc}: direct {host}:{port}", "no connection", r, r != "CONNECTED")


def main() -> int:
    # N01 - unapproved exchange hosts
    for h in (
        "api.kraken.com:443",
        "app.okx.com:443",
        "us.okx.com:443",
        "testnet.binance.vision:443",
    ):
        expect_denied_via_proxy("N01", "unapproved exchange host (runtime profile)", h)
    expect_unreachable_direct("N01", "unapproved exchange host", "api.kraken.com", 443)

    # N02 - production trading endpoints / hosts
    for h in (
        "api.binance.com:443",
        "api1.binance.com:443",
        "api.binance.us:443",
        "fapi.binance.com:443",
        "www.binance.com:443",
        "stream.binance.com:9443",
        "api.okx.com:443",
        "wspap.okx.com:8443",
        "ws.okx.com:8443",
    ):
        expect_denied_via_proxy("N02", "production/other trading host or port", h)
    expect_unreachable_direct("N02", "production trading host", "api.binance.com", 443)

    # N03 - arbitrary HTTP / HTTPS services
    st = raw_proxy(b"GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n")
    record("N03", "plain HTTP proxying request", "proxy refuses", st, denied(st))
    for h in ("example.com:443", "example.com:80", "httpbin.org:443", "ifconfig.me:443"):
        expect_denied_via_proxy("N03", "arbitrary external service", h)
    expect_unreachable_direct("N03", "arbitrary external HTTP", "example.com", 80)

    # N04 - direct IP destinations
    for ip, port in (
        ("1.1.1.1", 443),
        ("8.8.8.8", 53),
        ("93.184.216.34", 80),
        ("104.18.32.47", 443),
    ):
        expect_unreachable_direct("N04", "direct external IP", ip, port)
        expect_denied_via_proxy("N04", "IP literal via proxy", f"{ip}:{port}")

    # N05 - unapproved WebSocket endpoints (hosts/ports)
    for h in (
        "stream.binance.com:9443",
        "data-stream.binance.vision:8443",
        "ws.okx.com:8443",
        "wspap.okx.com:8443",
    ):
        expect_denied_via_proxy("N05", "unapproved WebSocket endpoint", h)

    # N06 - proxy bypass attempts
    expect_unreachable_direct(
        "N06", "bypass the proxy to an ALLOWED host", "data-api.binance.vision", 443
    )
    st = raw_proxy(b"\x05\x01\x00")  # SOCKS5 greeting
    record(
        "N06",
        "SOCKS handshake to the proxy port",
        "proxy refuses/closes",
        st,
        denied(st) or st.startswith("error") or st == "EMPTY",
    )
    expect_denied_via_proxy("N06", "tunnel to the proxy itself", "asta-egress-proxy:3128")
    for p in (1080, 3129, 8080, 8888, 9050):
        r = direct("asta-egress-proxy", p, timeout=2)
        record("N06", f"alternative proxy port {p}", "closed", r, r != "CONNECTED")
    st = raw_proxy(b"CONNECT data-api.binance.vision:443@evil.example:443 HTTP/1.1\r\n\r\n")
    record("N06", "userinfo/host confusion in CONNECT target", "proxy refuses", st, denied(st))
    st = raw_proxy(b"CONNECT DATA-API.BINANCE.VISION.:80 HTTP/1.1\r\n\r\n")
    record("N06", "case/trailing-dot variant on a wrong port", "proxy refuses", st, denied(st))

    # N07 - host-only management endpoints
    for host, port in (
        ("host.docker.internal", 5640),
        ("host.docker.internal", 2375),
        ("gateway.docker.internal", 22),
        ("kubernetes.docker.internal", 6443),
    ):
        expect_unreachable_direct("N07", "host-only service", host, port)
        expect_denied_via_proxy("N07", "host-only service via proxy", f"{host}:{port}")
    expect_denied_via_proxy("N07", "loopback via proxy", "localhost:5432")
    expect_denied_via_proxy("N07", "loopback IP via proxy", "127.0.0.1:5432")
    record(
        "N07",
        "docker socket absent in container",
        "no /var/run/docker.sock",
        str(os.path.exists("/var/run/docker.sock")),
        not os.path.exists("/var/run/docker.sock"),
    )

    # N08 - cloud metadata
    for h, p in (
        ("169.254.169.254", 80),
        ("169.254.169.254", 443),
        ("metadata.google.internal", 80),
        ("100.100.100.200", 80),
    ):
        expect_unreachable_direct("N08", "metadata endpoint", h, p)
        expect_denied_via_proxy("N08", "metadata endpoint via proxy", f"{h}:{p}")

    # N09 - IPv6
    expect_unreachable_direct("N09", "external IPv6", "2606:4700:4700::1111", 443, socket.AF_INET6)
    st = connect_via_proxy("[2606:4700:4700::1111]:443")
    record("N09", "IPv6 literal via proxy", "proxy refuses", st, denied(st))
    st = connect_via_proxy("[::1]:5432")
    record("N09", "IPv6 loopback via proxy", "proxy refuses", st, denied(st))
    try:
        v6 = [
            ln
            for ln in open("/proc/net/if_inet6").read().splitlines()
            if not ln.split()[-1] == "lo"
        ]
    except OSError:
        v6 = []
    record("N09", "no global IPv6 address on the container", "none", str(v6), not v6)

    # N10 - redirects to unauthorized hosts: the destination of any followed redirect still has to
    # pass the same proxy policy; a client that follows a redirect to these hosts is refused.
    for h in ("example.org:443", "evil.example:443", "api.binance.com:443"):
        expect_denied_via_proxy("N10", "redirect target (unapproved) must be refused", h)

    # N11 - positive control: approved public market-data host works through the proxy
    st = connect_via_proxy("data-api.binance.vision:443")
    ok_open = st.startswith("HTTP/1.1 200")
    record("N11", "approved public data host reachable via proxy", "tunnel opens", st, ok_open)
    if ok_open:
        try:
            s = socket.create_connection(PROXY, 5)
            s.sendall(b"CONNECT data-api.binance.vision:443 HTTP/1.1\r\n\r\n")
            s.recv(200)
            ctx = ssl.create_default_context()
            t = ctx.wrap_socket(s, server_hostname="data-api.binance.vision")
            t.settimeout(10)
            t.sendall(
                b"GET /api/v3/time HTTP/1.1\r\nHost: data-api.binance.vision\r\nConnection: close\r\n\r\n"
            )
            body = b""
            while True:
                c = t.recv(4096)
                if not c:
                    break
                body += c
            line = body.split(b"\r\n", 1)[0].decode()
            record(
                "N11",
                "GET /api/v3/time over TLS via the tunnel (public, unauthenticated)",
                "HTTP 200",
                line,
                line.startswith("HTTP/1.1 200"),
            )
        except Exception as exc:  # noqa: BLE001
            record(
                "N11", "TLS GET via tunnel", "HTTP 200", f"error:{type(exc).__name__}:{exc}", False
            )
    # OKX: report the truth; a sinkholed name must be refused, never claimed as connected
    st = connect_via_proxy("www.okx.com:443")
    record(
        "N11",
        "OKX public host via proxy (informational; upstream DNS sinkhole R-1)",
        "200 if lawfully reachable, else refused",
        st,
        True,
    )

    # N12 - container posture
    st_ = open("/proc/self/status").read()
    cap = [ln for ln in st_.splitlines() if ln.startswith("CapEff")][0].split()[1]
    nnp = [ln for ln in st_.splitlines() if ln.startswith("NoNewPrivs")][0].split()[1]
    record("N12", "not root", "uid != 0", str(os.getuid()), os.getuid() != 0)
    record("N12", "no effective capabilities", "CapEff == 0", cap, int(cap, 16) == 0)
    record("N12", "no-new-privileges", "1", nnp, nnp == "1")
    try:
        open("/rootfs-write-test", "w").write("x")
        w = "WRITABLE"
    except OSError as exc:
        w = f"read-only:{type(exc).__name__}"
    record("N12", "root filesystem read-only", "read-only", w, w.startswith("read-only"))
    mounts = open("/proc/mounts").read()
    # Host-backed mounts (9p/drvfs/virtiofs/fuse) other than the single read-only test file, and any
    # mountpoint that exposes the Docker socket or the host filesystem, are findings.
    expected_files = ("/app/negative_tests.py", "/app/capability_probe.py")
    bad = []
    for m in mounts.splitlines():
        f = m.split()
        mp, fstype, opts = f[1], f[2], f[3].split(",")
        host_backed = fstype in ("9p", "virtiofs", "fuse", "fuse.grpcfuse", "fuse.rclone")
        if "docker.sock" in mp or mp.startswith(("/mnt", "/host", "/Users", "/run/desktop")):
            bad.append(m[:120])
        elif host_backed and not (mp in expected_files and "ro" in opts):
            bad.append(m[:120])
    record("N12", "no host mounts except the two read-only test scripts", "none", str(bad), not bad)

    # N13 - DNS behaviour (informational evidence for DNS exfiltration/rebinding analysis)
    try:
        ip = socket.gethostbyname("example.com")
        record(
            "N13",
            "direct DNS resolution of an external name from the protected network",
            "informational",
            f"resolved:{ip}",
            True,
        )
    except Exception as exc:  # noqa: BLE001
        record(
            "N13",
            "direct DNS resolution of an external name from the protected network",
            "informational",
            f"no-resolve:{type(exc).__name__}",
            True,
        )

    failed = [r for r in RESULTS if not r["pass"]]
    print(json.dumps({"total": len(RESULTS), "failed": len(failed), "results": RESULTS}, indent=1))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
