"""Length-prefixed frames over a Unix-domain socket (local IPC: AF_UNIX only, no Internet socket).

The order path talks to the credential-side service through this channel. The socket file's
owner/group/mode is the first access control; every message is additionally authenticated
(``asta.orders.extwire``). Nothing here knows about orders, keys or exchanges: bytes in, bytes out,
hard size limit, hard timeouts, fail closed (it raises on any problem).
"""

from __future__ import annotations

import socket

MAX_FRAME = 8_192


def pack(data: bytes) -> bytes:
    if len(data) > MAX_FRAME:
        raise ValueError("frame too large")
    return len(data).to_bytes(4, "big") + data


def _recv_exact(s: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed the connection")
        buf += chunk
    return buf


def recv_frame(s: socket.socket) -> bytes:
    n = int.from_bytes(_recv_exact(s, 4), "big")
    if n > MAX_FRAME:
        raise ValueError("frame too large")
    return _recv_exact(s, n)


class UnixSocketChannel:
    """Callable ``bytes -> bytes`` (one request, one reply); raises on any trouble."""

    def __init__(self, path: str, timeout_s: float = 10.0) -> None:
        self._path, self._timeout = path, timeout_s

    def __call__(self, wire: bytes) -> bytes:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.settimeout(self._timeout)
            s.connect(self._path)
            s.sendall(pack(wire))
            return recv_frame(s)
        finally:
            s.close()
