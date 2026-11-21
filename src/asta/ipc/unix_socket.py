"""Length-prefixed frames over a Unix-domain socket (local IPC: AF_UNIX only, no Internet socket).

The order path talks to the credential-side service through this channel. The socket file's
owner/group/mode is the first access control; every message is additionally authenticated
(``asta.orders.extwire``). Nothing here knows about orders, keys or exchanges: bytes in, bytes out,
hard size limit, hard timeouts, fail closed (it raises on any problem).
"""

from __future__ import annotations

import socket
import struct
import sys
import time

MAX_FRAME = 8_192


def pack(data: bytes) -> bytes:
    if len(data) > MAX_FRAME:
        raise ValueError("frame too large")
    return len(data).to_bytes(4, "big") + data


def _recv_exact(s: socket.socket, n: int, deadline: float | None) -> bytes:
    buf = b""
    while len(buf) < n:
        if (
            deadline is not None
        ):  # an OVERALL deadline: a client that trickles bytes cannot hold the line
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError("frame deadline exceeded")
            s.settimeout(left)
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed the connection")
        buf += chunk
    return buf


def recv_frame(s: socket.socket, deadline: float | None = None) -> bytes:
    """Read one frame; ``deadline`` is an absolute monotonic time for the whole frame."""
    n = int.from_bytes(_recv_exact(s, 4, deadline), "big")
    if n > MAX_FRAME:
        raise ValueError("frame too large")
    return _recv_exact(s, n, deadline)


def peer_uid(s: socket.socket) -> int | None:
    """The peer user id reported by the kernel (LOCAL_PEERCRED / SO_PEERCRED), else None."""
    try:
        if sys.platform == "darwin":
            cred = s.getsockopt(0, 1, 76)  # SOL_LOCAL, LOCAL_PEERCRED -> struct xucred
            return int(struct.unpack_from("=II", cred)[1])  # cr_version, cr_uid
        if sys.platform.startswith("linux"):
            cred = s.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            return int(struct.unpack("3i", cred)[1])  # pid, uid, gid
    except OSError:
        return None
    return None


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
