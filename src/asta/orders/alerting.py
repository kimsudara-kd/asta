"""Off-host alerting pieces (M3-B-FINAL phase 5E; closes design gap G-13).

* ``extended_checks`` adds the remaining catalogue entries to ``monitor.check``:
  reconciliation discrepancy, persistent market-feed loss and backup failure (read-only).
* ``make_stamp`` / ``verify_stamp``: a signed application-status stamp that the firewall
  VM's dead-man job reads over the one-way status feed. A dead app, bridge or database
  stops refreshing the stamp (or marks it not-ok), so the heartbeat stops; a compromised
  app can at worst claim "ok", never keep a *dead* app looking alive.
* ``Notifier``: posts code + severity + time only (no secrets, hostnames or addresses) to
  a destination URL taken from the environment; the URL is a bearer secret and is never
  printed, logged or put in an exception.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from asta.orders.monitor import Alert

FEED_LOSS_AFTER_S = 600.0  # a LIVE feed that has not been CONNECTED / seen for 10 minutes
BACKUP_MAX_AGE_S = 26 * 3600.0  # the backup timer is daily
STAMP_MAX_AGE_S = 120.0
MAX_BODY = 512


def extended_checks(
    sessions: sessionmaker[Session],
    backup_dir: Path | None = None,
    now: float | None = None,
) -> list[Alert]:
    out: list[Alert] = []
    try:
        with sessions() as db:
            n = db.execute(
                text(
                    "SELECT count(*) FROM incidents WHERE kind = 'RECONCILIATION_MISMATCH'"
                    " AND resolved_at IS NULL"
                )
            ).scalar_one()
            feeds = db.execute(
                text(
                    "SELECT venue, state,"
                    " extract(epoch FROM now() - coalesce(last_event_at, since))"
                    " FROM market_feed_connections WHERE source = 'LIVE'"
                )
            ).all()
    except Exception as exc:  # noqa: BLE001
        return [Alert("DATABASE_UNREADABLE", "critical", type(exc).__name__)]
    if n:
        out.append(Alert("RECONCILIATION_DISCREPANCY", "critical", f"{n} unresolved"))
    for venue, state, age in feeds:
        if state != "CONNECTED" and float(age) > FEED_LOSS_AFTER_S:
            out.append(Alert("MARKET_FEED_LOSS", "warning", f"{venue} {state} {float(age):.0f}s"))
        elif state == "CONNECTED" and float(age) > FEED_LOSS_AFTER_S:
            out.append(Alert("MARKET_FEED_STALE", "warning", f"{venue} silent {float(age):.0f}s"))
    if backup_dir is not None:
        files = sorted(backup_dir.glob("asta-*.dump.age")) if backup_dir.is_dir() else []
        t = now if now is not None else time.time()
        if not files:
            out.append(Alert("BACKUP_MISSING", "critical", "no backup file"))
        elif t - max(f.stat().st_mtime for f in files) > BACKUP_MAX_AGE_S:
            out.append(Alert("BACKUP_STALE", "critical", "newest backup older than 26h"))
    return out


# ------------------------------------------------------------------ signed status stamp
def _mac(key: bytes, ts: int, ok: bool, codes: list[str]) -> str:
    msg = f"ts={ts};ok={int(ok)};codes={','.join(codes)}".encode()
    if not 16 <= len(key) <= 64:
        raise ValueError("status key must be 16..64 bytes")
    return hashlib.blake2b(msg, key=key, digest_size=32).hexdigest()  # keyed BLAKE2b (RFC 7693) MAC


def make_stamp(key: bytes, alerts: list[Alert], now: float | None = None) -> str:
    ts = int(now if now is not None else time.time())
    codes = sorted(a.code for a in alerts if a.severity == "critical")
    ok = not codes
    return f"ts={ts} ok={int(ok)} codes={','.join(codes)} mac={_mac(key, ts, ok, codes)}"


def verify_stamp(key: bytes, stamp: str, now: float | None = None) -> tuple[bool, str]:
    """(healthy, reason). Healthy only for a fresh, authentic, ok=1 stamp."""
    try:
        parts = dict(p.split("=", 1) for p in stamp.split())
        ts, ok, codes, mac = int(parts["ts"]), parts["ok"] == "1", parts["codes"], parts["mac"]
    except (KeyError, ValueError):
        return False, "MALFORMED"
    expect = _mac(key, ts, ok, [c for c in codes.split(",") if c])
    if not secrets.compare_digest(expect, mac):
        return False, "NOT_AUTHENTIC"
    t = now if now is not None else time.time()
    if t - ts > STAMP_MAX_AGE_S or ts - t > STAMP_MAX_AGE_S:
        return False, "STALE"
    return (True, "OK") if ok else (False, "APP_REPORTS_CRITICAL")


def write_stamp(path: Path, key: bytes, alerts: list[Alert]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(make_stamp(key, alerts) + "\n", encoding="ascii")
    tmp.replace(path)  # atomic: the reader never sees a half-written stamp


# ------------------------------------------------------------------ notifier
class NotifyError(Exception):
    """Delivery failed. The message never contains the destination."""


class Notifier:
    def __init__(
        self,
        url: str | None,
        opener: Callable[..., object] | None = None,
        timeout: float = 10.0,
    ) -> None:
        if url is not None and not url.startswith(
            ("https://", "http://127.0.0.1", "http://localhost")
        ):
            raise NotifyError("destination must be https (loopback http allowed for tests)")
        self._url = url
        self._open = opener or urllib.request.urlopen
        self._timeout = timeout

    @property
    def configured(self) -> bool:
        return self._url is not None

    def send(self, code: str, severity: str, now: float | None = None) -> bool:
        """Send code + severity + time only; False when unconfigured (nothing sent)."""
        if self._url is None:
            return False
        body = json.dumps(
            {"code": code[:48], "severity": severity[:12], "ts": int(now or time.time())}
        ).encode()
        assert len(body) < MAX_BODY
        req = urllib.request.Request(  # noqa: S310 - scheme checked in __init__
            self._url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            self._open(req, timeout=self._timeout)
        except Exception as exc:  # noqa: BLE001 - never leak the URL through the exception text
            raise NotifyError(f"delivery failed ({type(exc).__name__})") from None
        return True
