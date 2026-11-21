"""STAGED credential-side service (outside src/): receives authenticated requests from the order path, re-validates EVERYTHING,
then drives ``BinanceTestnetPort``. It is where caps, the venue/symbol allow-lists, the kill switch and the runtime enablement
are enforced a second time, independently of the app. It never returns a secret and never forwards what it did not validate.

Deployment (no VMs): runs as the local user ``asta-cred`` and serves a Unix-domain socket (see ``serve_unix``); the request
authentication key is shared with the app side only through a key file readable by both users. NOT deployed, NOT wired.
Hardening from the independent review: a SEND needs the service's OWN enablement check (a compromised app holding the MAC key
cannot trade while enablement is off), the day cap is persistent, no exception can kill the accept loop, sizes are bounded,
connections have an overall deadline, are handled concurrently, and can be restricted to given peer user ids.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol

from asta.core.environment import Environment
from asta.ipc.unix_socket import pack, peer_uid, recv_frame
from asta.orders.channel import Authenticator
from asta.orders.extwire import Reply, Request, RequestReceiver, seal_reply
from asta.orders.lifecycle import Intent
from asta.orders.ports import ExchangePort, QueryKind, SendOutcome

MAX_MAGNITUDE_EXP = 12  # |value| < 10**12: far beyond any sane order, far below Decimal overflow
MIN_MAGNITUDE_EXP = -18


class SpendStore(Protocol):
    def get(self, day: str) -> Decimal: ...

    def add(self, day: str, amount: Decimal) -> None: ...


class MemorySpend:
    """Process-local (tests, dry runs). A restart forgets it: deployments use ``FileSpendStore``."""

    def __init__(self) -> None:
        self._d: dict[str, Decimal] = {}

    def get(self, day: str) -> Decimal:
        return self._d.get(day, Decimal(0))

    def add(self, day: str, amount: Decimal) -> None:
        self._d[day] = self.get(day) + amount


@dataclass
class Caps:
    venue: str = "BINANCE_TESTNET"
    symbols: frozenset[str] = frozenset({"BTC-USDT", "ETH-USDT"})
    max_order_notional: Decimal = Decimal("20")
    max_day_notional: Decimal = Decimal("200")
    #: needed to size a MARKET order given by quantity; absent => such orders are refused (cap not verifiable)
    reference_price: Callable[[str], Decimal | None] | None = None
    spend: SpendStore = field(default_factory=MemorySpend)


def _sane(d: Decimal | None) -> bool:
    return (
        d is not None
        and d.is_finite()
        and d > 0
        and MIN_MAGNITUDE_EXP <= d.adjusted() < MAX_MAGNITUDE_EXP
    )


class CredentialService:
    def __init__(
        self,
        receiver: RequestReceiver,
        auth: Authenticator,
        port: ExchangePort,
        caps: Caps,
        kill_active: Callable[[], bool],
        clock: Callable[[], float],
        reply_key_id: str,
        *,
        execution_enabled: Callable[[], bool],
    ) -> None:
        self._rx, self._auth, self._port, self._caps = receiver, auth, port, caps
        self._kill, self._clock, self._key = kill_active, clock, reply_key_id
        self._enabled = execution_enabled
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ policy
    def _notional(self, i: Intent) -> Decimal | None:
        try:
            vals = [v for v in (i.quantity, i.limit_price, i.quote_notional) if v is not None]
            if not all(_sane(v) for v in vals):
                return None
            n: Decimal | None = None
            if i.order_type == "LIMIT" and i.quantity is not None and i.limit_price is not None:
                n = i.quantity * i.limit_price
            elif i.quote_notional is not None:
                n = i.quote_notional
            elif i.quantity is not None and self._caps.reference_price is not None:
                p = self._caps.reference_price(i.symbol)
                n = None if not _sane(p) else i.quantity * p  # type: ignore[operator]
            return n if _sane(n) else None
        except (ArithmeticError, InvalidOperation):
            return None

    def _day(self) -> str:
        return datetime.fromtimestamp(self._clock(), UTC).strftime("%Y-%m-%d")

    def _check_send(self, i: Intent) -> str:
        c = self._caps
        if i.environment is not Environment.EXCHANGE_DEMO:
            return "ENVIRONMENT_NOT_ALLOWED"
        if i.venue != c.venue:
            return "VENUE_NOT_ALLOWED"
        if i.symbol not in c.symbols:
            return "SYMBOL_NOT_ALLOWED"
        if self._kill():
            return "KILL_SWITCH_LATCHED"
        try:
            on = bool(self._enabled())
        except Exception:  # noqa: BLE001 - an unreadable enablement is a refusal
            on = False
        if not on:
            return "TESTNET_NOT_ENABLED"
        n = self._notional(i)
        if n is None:
            return "NOTIONAL_UNVERIFIABLE"
        if n > c.max_order_notional:
            return "ORDER_CAP_EXCEEDED"
        if c.spend.get(self._day()) + n > c.max_day_notional:
            return "DAY_CAP_EXCEEDED"
        return ""

    # ------------------------------------------------------------------ handling
    def handle(self, wire: bytes) -> bytes:
        try:
            with self._lock:
                req, why = self._rx.open(wire, int(self._clock()))
                if req is None:
                    return b"REFUSED:" + why.encode(
                        "ascii", "replace"
                    )  # unauthenticated: port says UNKNOWN
                return seal_reply(self._dispatch(req), self._auth)
        except Exception:  # noqa: BLE001 - no input may ever kill the service
            return b"REFUSED:INTERNAL_ERROR"

    def _reply(
        self, r: Request, outcome: str, state: str = "", code: str = "",
        data: tuple[tuple[str, ...], ...] = (),
    ) -> Reply:  # fmt: skip
        return Reply(self._key, r.nonce, r.op, outcome, state, code, data)

    def _dispatch(self, r: Request) -> Reply:
        if r.op == "SEND":
            assert r.intent is not None
            why = self._check_send(r.intent)
            if why:
                return self._reply(r, "REFUSED", code=why)
            n = self._notional(r.intent) or Decimal(0)
            try:
                out = self._port.send(r.intent)
            except ValueError:  # the connector refused to even build the request: nothing was sent
                return self._reply(r, "REFUSED", code="INVALID_ORDER")
            except Exception:  # noqa: BLE001 - it MAY have reached the venue: count it, report UNKNOWN
                out = SendOutcome.UNKNOWN
            if out in (
                SendOutcome.ACCEPTED,
                SendOutcome.UNKNOWN,
            ):  # may exist at the venue: it counts
                self._caps.spend.add(self._day(), n)
            return self._reply(r, out.value)
        if r.symbol not in self._caps.symbols:
            return self._reply(r, "REFUSED", code="SYMBOL_NOT_ALLOWED")
        bind = getattr(self._port, "bind_symbol", None)
        if bind is not None and r.op != "BALANCES":
            bind(r.client_id, r.symbol)
        try:
            if (
                r.op == "BALANCES"
            ):  # read-only; never blocked by the kill switch (it only reveals the truth)
                rows = self._port.balances()  # type: ignore[attr-defined]
                if rows is None or len(rows) > 60:
                    return self._reply(r, "UNAVAILABLE")
                return self._reply(r, QueryKind.FOUND.value, data=tuple(rows))
            if r.op == "TRADES":  # read-only trades of one order, a page at a time
                kind, trade_rows, more = self._port.trades(r.client_id, r.skip)  # type: ignore[attr-defined]
                return self._reply(r, kind.value, "MORE" if more else "", data=tuple(trade_rows))
            if r.op == "QUERY":
                q = self._port.query(r.client_id)
                if q.kind is QueryKind.FOUND and q.view is not None:
                    return self._reply(r, q.kind.value, q.view.state.value)
                return self._reply(r, q.kind.value)
            c = self._port.cancel(
                r.client_id
            )  # CANCEL: allowed even while the kill switch is latched
            return self._reply(r, c.outcome.value, c.state.value if c.state else "")
        except Exception:  # noqa: BLE001
            return self._reply(r, "UNAVAILABLE" if r.op == "QUERY" else "UNKNOWN")


def serve_unix(
    path: str,
    service: CredentialService,
    stop: threading.Event,
    mode: int = 0o660,
    allowed_uids: frozenset[int] | None = None,
    deadline_s: float = 5.0,
    max_workers: int = 8,
) -> None:
    """Accept loop on a Unix-domain socket: one request per connection, an overall deadline per frame, a bounded number of
    concurrent connections, optional peer-uid allow-list, and no exception can end the loop."""
    if os.path.exists(path):
        os.unlink(path)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o117)
    try:
        srv.bind(path)
    finally:
        os.umask(old)
    os.chmod(path, mode)
    srv.listen(16)
    srv.settimeout(0.2)
    slots = threading.BoundedSemaphore(max_workers)

    def serve_one(conn: socket.socket) -> None:
        try:
            with conn:
                if allowed_uids is not None and peer_uid(conn) not in allowed_uids:
                    return  # not one of the permitted users: no reply at all
                try:
                    wire = recv_frame(conn, time.monotonic() + deadline_s)
                    conn.settimeout(deadline_s)
                    conn.sendall(pack(service.handle(wire)))
                except Exception:  # noqa: BLE001 - never answer a malformed/slow peer, never die
                    return
        finally:
            slots.release()

    try:
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except TimeoutError:
                continue
            except OSError:
                continue
            if not slots.acquire(blocking=False):  # saturated: shed load instead of queueing
                conn.close()
                continue
            threading.Thread(target=serve_one, args=(conn,), daemon=True).start()
    finally:
        srv.close()
        if os.path.exists(path):
            os.unlink(path)
