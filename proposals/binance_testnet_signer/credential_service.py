"""STAGED credential-side service (outside src/): receives authenticated requests from the order path, re-validates EVERYTHING,
then drives ``BinanceTestnetPort``. It is where caps, the venue/symbol allow-lists and the kill switch are enforced a second
time, independently of the app. It never returns a secret and never forwards anything it did not validate.

Deployment (no VMs): runs as the local user ``asta-cred`` and serves a Unix-domain socket (see ``serve_unix``); the request
authentication key is shared with the app side only through a key file readable by both users. NOT deployed, NOT wired.
"""

from __future__ import annotations

import os
import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from asta.core.environment import Environment
from asta.ipc.unix_socket import pack, recv_frame
from asta.orders.channel import Authenticator
from asta.orders.extwire import Reply, Request, RequestReceiver, seal_reply
from asta.orders.lifecycle import Intent
from asta.orders.ports import CancelOutcome, ExchangePort, QueryKind, SendOutcome


@dataclass
class Caps:
    venue: str = "BINANCE_TESTNET"
    symbols: frozenset[str] = frozenset({"BTC-USDT", "ETH-USDT"})
    max_order_notional: Decimal = Decimal("20")
    max_day_notional: Decimal = Decimal("200")
    #: needed to size a MARKET order given by quantity; absent => such orders are refused (cap not verifiable)
    reference_price: Callable[[str], Decimal | None] | None = None
    _day: str = field(default="", init=False)
    _spent: Decimal = field(default=Decimal(0), init=False)


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
    ) -> None:
        self._rx, self._auth, self._port, self._caps = receiver, auth, port, caps
        self._kill, self._clock, self._key = kill_active, clock, reply_key_id
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ policy
    def _notional(self, i: Intent) -> Decimal | None:
        if i.order_type == "LIMIT" and i.quantity is not None and i.limit_price is not None:
            return i.quantity * i.limit_price
        if i.quote_notional is not None:
            return i.quote_notional
        if i.quantity is not None and self._caps.reference_price is not None:
            p = self._caps.reference_price(i.symbol)
            return None if p is None else i.quantity * p
        return None

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
        n = self._notional(i)
        if n is None or n <= 0:
            return "NOTIONAL_UNVERIFIABLE"
        if n > c.max_order_notional:
            return "ORDER_CAP_EXCEEDED"
        day = datetime.fromtimestamp(self._clock(), UTC).strftime("%Y-%m-%d")
        if c._day != day:
            c._day, c._spent = day, Decimal(0)
        if c._spent + n > c.max_day_notional:
            return "DAY_CAP_EXCEEDED"
        return ""

    # ------------------------------------------------------------------ handling
    def handle(self, wire: bytes) -> bytes:
        with self._lock:
            req, why = self._rx.open(wire, int(self._clock()))
            if req is None:
                return b"REFUSED:" + why.encode(
                    "ascii", "replace"
                )  # unauthenticated: the port treats it as UNKNOWN
            return seal_reply(self._dispatch(req), self._auth)

    def _reply(self, r: Request, outcome: str, state: str = "", code: str = "") -> Reply:
        return Reply(self._key, r.nonce, r.op, outcome, state, code)

    def _dispatch(self, r: Request) -> Reply:
        if r.op == "SEND":
            assert r.intent is not None
            why = self._check_send(r.intent)
            if why:
                return self._reply(r, "REFUSED", code=why)
            out = self._port.send(r.intent)
            if out in (
                SendOutcome.ACCEPTED,
                SendOutcome.UNKNOWN,
            ):  # may exist at the venue: it counts against the cap
                n = self._notional(r.intent) or Decimal(0)
                self._caps._spent += n
            return self._reply(r, out.value)
        if r.symbol not in self._caps.symbols:
            return self._reply(r, "REFUSED", code="SYMBOL_NOT_ALLOWED")
        self._bind(r)
        if r.op == "QUERY":
            q = self._port.query(r.client_id)
            if q.kind is QueryKind.FOUND and q.view is not None:
                return self._reply(r, q.kind.value, q.view.state.value)
            return self._reply(r, q.kind.value)
        c = self._port.cancel(r.client_id)  # CANCEL: allowed even while the kill switch is latched
        return self._reply(r, c.outcome.value, c.state.value if c.state else "")

    def _bind(self, r: Request) -> None:
        bind = getattr(self._port, "bind_symbol", None)
        if bind is not None:
            bind(r.client_id, r.symbol)


def serve_unix(
    path: str, service: CredentialService, stop: threading.Event, mode: int = 0o660
) -> None:
    """Blocking accept loop on a Unix-domain socket (one request per connection, size-limited, timeouts)."""
    if os.path.exists(path):
        os.unlink(path)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o117)
    try:
        srv.bind(path)
    finally:
        os.umask(old)
    os.chmod(path, mode)
    srv.listen(8)
    srv.settimeout(0.2)
    try:
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except TimeoutError:
                continue
            with conn:
                conn.settimeout(5.0)
                try:
                    conn.sendall(pack(service.handle(recv_frame(conn))))
                except (OSError, ValueError):
                    continue  # never answer a malformed/slow peer
    finally:
        srv.close()
        if os.path.exists(path):
            os.unlink(path)


_ = CancelOutcome  # (re-exported for tests)
