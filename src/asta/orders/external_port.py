"""``ExternalChannelPort``: an ``ExchangePort`` that carries each operation to the credential side.

It contains NO exchange signing, NO exchange key, NO HTTP and NO socket: bytes go out through an
injected ``channel`` (``bytes -> bytes``; in deployment a Unix-domain socket, see ``asta.ipc``). The
only secret it can reach is the shared message-authentication key, held by an ``Authenticator``. It
is only used behind ``GatedExchange`` (kind EXTERNAL), and the gate denies every EXTERNAL call while
the execution gateway is DENY, which it still is.

Mapping is conservative: an unreachable, slow, unauthentic or mismatched reply is UNKNOWN or
UNAVAILABLE, never a guess. A policy refusal by the service (caps, kill switch, venue, symbol) is an
explicit REJECTED: nothing exists at the venue.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable

from asta.orders.channel import Authenticator
from asta.orders.extwire import Reply, Request, open_reply, seal_request
from asta.orders.lifecycle import Intent, State
from asta.orders.ports import (
    CancelOutcome,
    CancelResult,
    ExchangeView,
    QueryKind,
    QueryResult,
    SendOutcome,
)

Channel = Callable[[bytes], bytes]


class NotSent(Exception):
    """The request could not even be encoded (size/range): nothing left this process."""


class ExternalChannelPort:
    def __init__(
        self,
        channel: Channel,
        auth: Authenticator,
        sender: str,
        key_id: str,
        clock: Callable[[], float],
        validity_s: int = 30,
    ) -> None:
        self._channel, self._auth = channel, auth
        self._sender, self._key_id, self._clock, self._validity = sender, key_id, clock, validity_s
        self._seq = 0
        self._symbols: dict[str, str] = {}

    # the gate calls this before query/cancel, so a restart can still address an order by id
    def bind_intent(self, intent: Intent) -> None:
        self._symbols[intent.decision_id] = intent.symbol

    def _next_seq(self) -> int:
        self._seq = max(self._seq + 1, int(self._clock() * 1_000_000))  # monotonic across restarts
        return self._seq

    def _roundtrip(
        self, op: str, client_id: str, symbol: str, intent: Intent | None
    ) -> Reply | None:
        now = int(self._clock())
        req = Request(
            sender=self._sender,
            key_id=self._key_id,
            seq=self._next_seq(),
            nonce=secrets.token_hex(16),
            issued_at=now,
            expires_at=now + self._validity,
            op=op,
            client_id=client_id,
            symbol=symbol,
            intent=intent,
        )
        try:
            sealed = seal_request(req, self._auth)
        except ValueError as exc:  # too large / out of range: nothing was sent
            raise NotSent from exc
        try:
            wire = self._channel(sealed)
        except (TimeoutError, OSError, ValueError):
            return None
        return open_reply(wire, self._auth, frozenset({self._key_id}), req.nonce, op)

    # ------------------------------------------------------------------ ExchangePort
    def send(self, intent: Intent) -> SendOutcome:
        self.bind_intent(intent)
        try:
            r = self._roundtrip("SEND", intent.decision_id, intent.symbol, intent)
        except NotSent:
            return SendOutcome.REJECTED
        if r is None:
            return SendOutcome.UNKNOWN
        if r.outcome == "REFUSED":
            return (
                SendOutcome.REJECTED
            )  # the service refused by policy: nothing was sent to the venue
        try:
            return SendOutcome(r.outcome)
        except ValueError:
            return SendOutcome.UNKNOWN

    def query(self, client_id: str) -> QueryResult:
        sym = self._symbols.get(client_id)
        if sym is None:
            return QueryResult(QueryKind.UNAVAILABLE)
        r = self._roundtrip("QUERY", client_id, sym, None)  # ids only: cannot fail to encode
        if r is None:
            return QueryResult(QueryKind.UNAVAILABLE)
        try:
            kind = QueryKind(r.outcome)
        except ValueError:
            return QueryResult(QueryKind.UNAVAILABLE)
        if kind is QueryKind.FOUND:
            try:
                return QueryResult(kind, ExchangeView(client_id, State(r.state)))
            except ValueError:
                return QueryResult(QueryKind.UNAVAILABLE)
        return QueryResult(kind)

    def cancel(self, client_id: str) -> CancelResult:
        sym = self._symbols.get(client_id)
        if sym is None:
            return CancelResult(CancelOutcome.UNKNOWN)
        r = self._roundtrip("CANCEL", client_id, sym, None)
        if r is None:
            return CancelResult(CancelOutcome.UNKNOWN)
        try:
            out = CancelOutcome(r.outcome)
            return CancelResult(out, State(r.state) if r.state else None)
        except ValueError:
            return CancelResult(CancelOutcome.UNKNOWN)
