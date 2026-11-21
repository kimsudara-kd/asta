"""Off-host heartbeat (no-VM design): the monitor pings an external dead-man service when healthy.

Silence is the alarm: if the Mac is off, the app hangs or the monitor dies, the pings
stop and the SERVICE alerts. On a critical fault the monitor pings the service's ``/fail``
path (an immediate alert). The URL is a bearer secret: it is read from a private file, must
be https (or exact loopback http for tests), redirects are refused, and no exception text,
log line or return value ever contains it.
"""

from __future__ import annotations

import os
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

from asta.orders.alerting import _destination_allowed, _no_redirect_open


class HeartbeatError(Exception):
    """Delivery or configuration failed. The message never contains the destination."""


def load_url_file(path: str) -> str:
    st = os.stat(path)
    if st.st_mode & 0o077:
        raise HeartbeatError("heartbeat URL file must be private (mode 0600 or stricter)")
    if st.st_uid not in (os.getuid(), 0):
        raise HeartbeatError("heartbeat URL file must be owned by this user")
    lines = [ln.strip() for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]
    if len(lines) != 1:
        raise HeartbeatError("heartbeat URL file must contain exactly one URL")
    return lines[0]


def fail_url(url: str) -> str:
    """``<path>/fail`` while keeping any query string intact (review R-10)."""
    p = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(p._replace(path=p.path.rstrip("/") + "/fail"))


class Heartbeat:
    def __init__(
        self, url: str, opener: Callable[..., object] | None = None, timeout: float = 10.0
    ) -> None:
        if not _destination_allowed(url):
            raise HeartbeatError("destination must be https (loopback http allowed for tests)")
        self._url, self._open, self._timeout = url, opener or _no_redirect_open, timeout

    def ping(self, ok: bool) -> bool:
        target = self._url if ok else fail_url(self._url)
        req = urllib.request.Request(target, method="GET")  # noqa: S310 - scheme checked in __init__
        try:
            self._open(req, timeout=self._timeout)
        except Exception as exc:  # noqa: BLE001 - never leak the URL through the exception text
            raise HeartbeatError(f"heartbeat failed ({type(exc).__name__})") from None
        return True
