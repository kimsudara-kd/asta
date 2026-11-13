"""ASTA egress proxy (M3A-1): the ONLY path from the protected network to the Internet.

A minimal CONNECT-only, default-deny forward proxy. It is infrastructure, not application logic:
the protected runtime sits on a Docker network with no route out (internal network), so a process
that ignores this proxy simply cannot connect. The proxy itself enforces, per CONNECT request:

* only the CONNECT method (no plain-HTTP proxying, no absolute-URI requests, no other verbs);
* the target must be a DNS NAME (IP literals - IPv4 or IPv6 - are refused, so direct-IP bypass fails);
* the (name, port) pair must be in the version-controlled allowlist of the selected profile;
* the name is resolved HERE, IPv4 only (IPv6 is refused); EVERY resolved address must be a public
  unicast address - loopback, private, link-local (169.254.0.0/16 incl. cloud metadata), CGNAT,
  multicast, reserved and unspecified are refused (DNS-rebinding / sinkhole / SSRF protection);
* the connection is made to the validated IP (pinned), never re-resolved;
* limits: header size, concurrent connections, idle and total timeouts;
* every decision is logged as one JSON line to stdout (no payloads, no secrets exist here).

Limits that a CONNECT proxy cannot remove (documented in docs/m3a/EGRESS-ENFORCEMENT.md): the TLS
payload (HTTP path, headers) is opaque, so "same host, different path" (e.g. OKX demo vs production
share a host) is NOT separable here; path/credential controls remain application-level.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import socket
import sys
import time
from pathlib import Path

ALLOWLIST_PATH = Path(os.environ.get("EGRESS_ALLOWLIST", "/etc/asta-egress/allowlist.json"))
PROFILE = os.environ.get("ASTA_EGRESS_PROFILE", "runtime")
LISTEN_HOST = os.environ.get("EGRESS_LISTEN_HOST", "0.0.0.0")  # noqa: S104 - internal network only
LISTEN_PORT = int(os.environ.get("EGRESS_LISTEN_PORT", "3128"))
MAX_HEADER_BYTES = 4096
MAX_CONNECTIONS = int(os.environ.get("EGRESS_MAX_CONNECTIONS", "64"))
IDLE_TIMEOUT_S = 120
TOTAL_TIMEOUT_S = 24 * 3600
CONNECT_TIMEOUT_S = 10

_HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def log(decision: str, host: str, port: int | str, reason: str, peer: str, **extra: object) -> None:
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "decision": decision,
        "profile": PROFILE,
        "host": host[:255],
        "port": port,
        "reason": reason,
        "client": peer,
        **extra,
    }
    sys.stdout.write(json.dumps(rec) + "\n")
    sys.stdout.flush()


def load_allowlist(path: Path = ALLOWLIST_PATH, profile: str = PROFILE) -> dict[str, set[int]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data["profiles"][profile]
    return {e["host"].lower(): {int(p) for p in e["ports"]} for e in entries}


def is_public_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        return False  # IPv6 is not permitted at all
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or ip in ipaddress.ip_network("100.64.0.0/10")  # CGNAT
        or ip in ipaddress.ip_network("192.0.0.0/24")
        or ip in ipaddress.ip_network("198.18.0.0/15")
    )


def check_target(host: str, port: int, allow: dict[str, set[int]]) -> tuple[bool, str]:
    """Pure policy check on the requested (name, port); resolution is checked separately."""
    if host != host.lower() or host.endswith("."):
        host = host.lower().rstrip(".")
    try:
        ipaddress.ip_address(host.strip("[]"))
        return False, "ip-literal-denied"
    except ValueError:
        pass
    if "@" in host or ":" in host or "%" in host or not _HOST_RE.match(host):
        return False, "malformed-host"
    if host not in allow:
        return False, "host-not-allowlisted"
    if port not in allow[host]:
        return False, "port-not-allowlisted"
    return True, "allowlisted"


async def resolve_public_ipv4(host: str) -> tuple[str | None, str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM), 5
        )
    except Exception as exc:  # noqa: BLE001
        return None, f"dns-failure:{type(exc).__name__}"
    v4: list[str] = []
    for fam, _t, _p, _c, sa in infos:
        ip = ipaddress.ip_address(sa[0])
        if fam == socket.AF_INET6:
            continue  # ignored; only IPv4 may be used
        if not is_public_address(ip):  # ANY non-public answer poisons the whole name
            return None, f"resolves-to-non-public:{ip}"
        v4.append(str(ip))
    if not v4:
        return None, "no-public-ipv4-address"
    return v4[0], "ok"


async def pipe(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await asyncio.wait_for(r.read(65536), IDLE_TIMEOUT_S)
            if not data:
                break
            w.write(data)
            await w.drain()
    except Exception:  # noqa: BLE001, S110
        pass
    finally:
        try:
            w.close()
        except Exception:  # noqa: BLE001, S110
            pass


async def deny(w: asyncio.StreamWriter, code: int, text: str) -> None:
    w.write(f"HTTP/1.1 {code} {text}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n".encode())
    try:
        await w.drain()
    finally:
        w.close()


_sem: asyncio.Semaphore | None = None


async def handle(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, allow: dict[str, set[int]]
) -> None:
    peer = str((writer.get_extra_info("peername") or ("?",))[0])
    assert _sem is not None
    if _sem.locked():
        log("DENY", "?", "?", "too-many-connections", peer)
        await deny(writer, 503, "Busy")
        return
    async with _sem:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
        except Exception:  # noqa: BLE001
            log("DENY", "?", "?", "bad-or-oversized-request", peer)
            await deny(writer, 400, "Bad Request")
            return
        if len(head) > MAX_HEADER_BYTES:
            log("DENY", "?", "?", "header-too-large", peer)
            await deny(writer, 431, "Request Header Fields Too Large")
            return
        line = head.split(b"\r\n", 1)[0].decode("latin-1")
        parts = line.split(" ")
        if len(parts) != 3 or parts[0] != "CONNECT":
            log("DENY", line[:100], "?", "method-not-connect", peer)
            await deny(writer, 405, "Method Not Allowed")
            return
        target = parts[1]
        host, _, port_s = target.rpartition(":")
        if not host or not port_s.isdigit() or not (0 < int(port_s) < 65536):
            log("DENY", target, "?", "malformed-target", peer)
            await deny(writer, 400, "Bad Request")
            return
        port = int(port_s)
        ok, why = (
            check_target(host.strip("[]"), port, allow)
            if not host.startswith("[")
            else (False, "ip-literal-denied")
        )
        if not ok:
            log("DENY", host, port, why, peer)
            await deny(writer, 403, "Forbidden")
            return
        host = host.lower().rstrip(".")
        ip, why = await resolve_public_ipv4(host)
        if ip is None:
            log("DENY", host, port, why, peer)
            await deny(writer, 403, "Forbidden")
            return
        try:
            ur, uw = await asyncio.wait_for(asyncio.open_connection(ip, port), CONNECT_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001
            log("DENY", host, port, f"upstream-connect-failed:{type(exc).__name__}", peer, ip=ip)
            await deny(writer, 502, "Bad Gateway")
            return
        log("ALLOW", host, port, "allowlisted", peer, ip=ip)
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        try:
            await asyncio.wait_for(
                asyncio.gather(pipe(reader, uw), pipe(ur, writer)), TOTAL_TIMEOUT_S
            )
        except Exception:  # noqa: BLE001, S110
            pass
        finally:
            for w in (uw, writer):
                try:
                    w.close()
                except Exception:  # noqa: BLE001, S110
                    pass


async def main() -> None:
    global _sem
    allow = load_allowlist()
    digest = hashlib.sha256(ALLOWLIST_PATH.read_bytes()).hexdigest()
    _sem = asyncio.Semaphore(MAX_CONNECTIONS)
    server = await asyncio.start_server(
        lambda r, w: handle(r, w, allow), LISTEN_HOST, LISTEN_PORT, limit=MAX_HEADER_BYTES
    )
    log("START", "-", LISTEN_PORT, "listening", "-", allowlist_sha256=digest, hosts=sorted(allow))
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
