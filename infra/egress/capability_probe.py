"""Public, UNAUTHENTICATED capability probe, run INSIDE the protected container through the egress
proxy (profile: capability_probe). Only GET on public market-data paths and one WebSocket upgrade
handshake. No credentials, no signed request, no order/account endpoint.

    docker exec asta-protected-probe python /app/capability_probe.py
"""

from __future__ import annotations

import base64
import json
import os
import socket
import ssl
import sys
import time

# the same suite runs in Docker (default) and on the dedicated VMs (ASTA_PROXY=10.77.0.1:3128)
_PX = os.environ.get("ASTA_PROXY", "asta-egress-proxy:3128").rsplit(":", 1)
PROXY = (_PX[0], int(_PX[1]))
OUT: list[dict[str, object]] = []


def tunnel(host: str, port: int) -> tuple[socket.socket | None, str]:
    s = socket.create_connection(PROXY, 6)
    s.settimeout(10)
    s.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
    status = s.recv(300).split(b"\r\n", 1)[0].decode("latin-1")
    if not status.startswith("HTTP/1.1 200"):
        s.close()
        return None, status
    return s, status


def http_get(host: str, path: str) -> dict[str, object]:
    rec: dict[str, object] = {"kind": "REST GET", "host": host, "path": path}
    try:
        s, status = tunnel(host, 443)
        rec["proxy"] = status
        if s is None:
            rec["result"] = "BLOCKED"
            return rec
        t = ssl.create_default_context().wrap_socket(s, server_hostname=host)
        t.settimeout(12)
        t.sendall(
            f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: asta-m3a-probe\r\nConnection: close\r\n\r\n".encode()
        )
        buf = b""
        while len(buf) < 400000:
            c = t.recv(8192)
            if not c:
                break
            buf += c
        head, _, body = buf.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        rec["status"] = lines[0]
        hdrs = {
            ln.split(":", 1)[0].lower(): ln.split(":", 1)[1].strip()
            for ln in lines[1:]
            if ":" in ln
        }
        rec["headers_of_interest"] = {
            k: v
            for k, v in hdrs.items()
            if k.startswith(("x-mbx", "x-mbx-used", "retry-after", "server", "date", "location"))
        }
        rec["body_prefix"] = body[:600].decode("utf-8", "replace")
        rec["body_bytes"] = len(body)
        rec["result"] = "OK" if lines[0].startswith("HTTP/1.1 200") else "HTTP-ERROR"
    except Exception as exc:  # noqa: BLE001
        rec["result"] = f"ERROR:{type(exc).__name__}:{exc}"
    return rec


def ws_upgrade(host: str, port: int, path: str) -> dict[str, object]:
    rec: dict[str, object] = {"kind": "WS upgrade", "host": host, "port": port, "path": path}
    try:
        s, status = tunnel(host, port)
        rec["proxy"] = status
        if s is None:
            rec["result"] = "BLOCKED"
            return rec
        t = ssl.create_default_context().wrap_socket(s, server_hostname=host)
        t.settimeout(12)
        key = base64.b64encode(os.urandom(16)).decode()
        t.sendall(
            f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode()
        )
        rec["status"] = t.recv(400).split(b"\r\n", 1)[0].decode("latin-1")
        rec["result"] = "OK" if "101" in str(rec["status"]) else "HTTP-ERROR"
        t.close()
    except Exception as exc:  # noqa: BLE001
        rec["result"] = f"ERROR:{type(exc).__name__}:{exc}"
    return rec


def main() -> int:
    t0 = time.time()
    plan_http = [
        ("testnet.binance.vision", "/api/v3/ping"),
        ("testnet.binance.vision", "/api/v3/time"),
        ("testnet.binance.vision", "/api/v3/exchangeInfo?symbol=BTCUSDT"),
        ("testnet.binance.vision", "/api/v3/depth?symbol=BTCUSDT&limit=5"),
        ("testnet.binance.vision", "/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=2"),
        ("data-api.binance.vision", "/api/v3/exchangeInfo?symbol=BTCUSDT"),
        ("data-api.binance.vision", "/api/v3/depth?symbol=BTCUSDT&limit=5"),
        ("www.okx.com", "/api/v5/public/time"),
        ("www.okx.com", "/api/v5/public/instruments?instType=SPOT&instId=BTC-USDT"),
    ]
    for h, p in plan_http:
        OUT.append(http_get(h, p))
    OUT.append(ws_upgrade("stream.testnet.binance.vision", 443, "/ws/btcusdt@trade"))
    OUT.append(ws_upgrade("data-stream.binance.vision", 443, "/ws/btcusdt@trade"))
    OUT.append(ws_upgrade("ws.okx.com", 443, "/ws/v5/public"))
    print(json.dumps({"started": t0, "results": OUT}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
