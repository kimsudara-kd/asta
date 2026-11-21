"""STAGED PROPOSAL (not under src/, not imported by the application): Binance Spot TESTNET exchange port.

Implements ``asta.orders.ports.ExchangePort`` (send / query / cancel) over the staged signer. It has NO network code of
its own: the HTTP transport is injected (tests use the in-process mock exchange; a real transport is a separate
reviewed step that must go through the egress boundary). It is Spot Testnet only: every request goes through
``build_signed_request`` (testnet host, spot path allow-list, per-path parameter allow-list, recvWindow <= 5000).

Outcome mapping is deliberately conservative: anything ambiguous is UNKNOWN (the dispatcher then learns the truth by
query, and only re-sends after repeated NOT_FOUND). The client order id is the decision id, so a re-send of the same
decision can never create a second order (the venue answers "Duplicate order sent" -> DUPLICATE_ID).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from decimal import Decimal
from typing import Any, Protocol

from signer import SignedRequest, Signer, SignerUnavailable, build_signed_request

from asta.orders.lifecycle import Intent, State
from asta.orders.ports import (
    CancelOutcome,
    CancelResult,
    ExchangeView,
    QueryKind,
    QueryResult,
    SendOutcome,
)

VENUE = "BINANCE_TESTNET"
_CLIENT_ID = re.compile(r"[A-Za-z0-9_-]{1,36}")  # the venue's newClientOrderId rule

_STATUS = {
    "NEW": State.ACCEPTED,
    "PENDING_CANCEL": State.ACCEPTED,  # still live until the cancel completes
    "PARTIALLY_FILLED": State.PARTIALLY_FILLED,
    "FILLED": State.FILLED,
    "CANCELED": State.CANCELED,
    "REJECTED": State.REJECTED,
    "EXPIRED": State.EXPIRED,
    "EXPIRED_IN_MATCH": State.EXPIRED,
}
_TERMINAL = {State.FILLED, State.CANCELED, State.REJECTED, State.EXPIRED}


class Transport(Protocol):
    """Sends one already-signed request. Returns (http_status, json_body). Raises TimeoutError/OSError on transport
    trouble (the connector maps those to UNKNOWN). Must never be given credentials: the signature is in the URL."""

    def __call__(self, request: SignedRequest, timeout_s: float) -> tuple[int, dict[str, Any]]: ...


def _plain(d: Decimal) -> str:
    return format(d.normalize(), "f")


def _venue_symbol(symbol: str) -> str:
    out = symbol.replace("-", "")
    if not re.fullmatch(r"[A-Z0-9]{5,20}", out):
        raise ValueError("unsupported symbol")
    return out


def order_params(intent: Intent) -> dict[str, str]:
    """The exact, minimal parameter set for a spot order (anything else is refused by the signer)."""
    intent.validate()
    if intent.venue != VENUE:
        raise ValueError("this port only serves the Binance Spot Testnet venue")
    if not _CLIENT_ID.fullmatch(intent.decision_id):
        raise ValueError("decision id is not a valid client order id")
    p = {
        "symbol": _venue_symbol(intent.symbol),
        "side": intent.side,
        "type": intent.order_type,
        "newClientOrderId": intent.decision_id,
        "newOrderRespType": "RESULT",
    }
    if intent.order_type == "LIMIT":
        if intent.quantity is None or intent.limit_price is None:
            raise ValueError("LIMIT needs quantity and limit_price")
        p.update(
            {
                "timeInForce": "GTC",
                "quantity": _plain(intent.quantity),
                "price": _plain(intent.limit_price),
            }
        )
    else:
        if (intent.quantity is None) == (intent.quote_notional is None):
            raise ValueError("MARKET needs exactly one of quantity / quote_notional")
        if intent.quote_notional is not None:
            if intent.side != "BUY":
                raise ValueError("quote_notional is only valid for BUY")
            p["quoteOrderQty"] = _plain(intent.quote_notional)
        else:
            assert intent.quantity is not None
            p["quantity"] = _plain(intent.quantity)
    return p


class BinanceTestnetPort:
    def __init__(
        self,
        signer: Signer,
        transport: Transport,
        clock_ms: Callable[[], int],
        api_key_id: str,
        recv_window_ms: int = 5000,
        timeout_s: float = 10.0,
    ) -> None:
        self._signer, self._t, self._now = signer, transport, clock_ms
        self._key, self._rw, self._timeout = api_key_id, recv_window_ms, timeout_s
        self._symbols = {}

    # ------------------------------------------------------------------ plumbing
    def _call(self, method: str, params: dict[str, str]) -> tuple[int, dict[str, Any]] | None:
        """None = the request may or may not have reached the venue (timeout / transport error)."""
        req = build_signed_request(
            self._signer,
            method,
            "/api/v3/order",
            params,
            self._now(),
            self._rw,
            api_key_id=self._key,
        )
        try:
            return self._t(req, self._timeout)
        except (TimeoutError, OSError):
            return None

    @staticmethod
    def _code(body: dict[str, Any]) -> int | None:
        c = body.get("code")
        return c if isinstance(c, int) else None

    # ------------------------------------------------------------------ ExchangePort
    def send(self, intent: Intent) -> SendOutcome:
        params = order_params(intent)
        self._symbols[intent.decision_id] = params["symbol"]
        try:
            res = self._call("POST", params)
        except SignerUnavailable:
            return SendOutcome.REJECTED  # nothing left this process: nothing exists at the venue
        if res is None:
            return SendOutcome.UNKNOWN
        status, body = res
        if status == 200:
            st = _STATUS.get(str(body.get("status")))
            if st in (State.ACCEPTED, State.PARTIALLY_FILLED, State.FILLED):
                return SendOutcome.ACCEPTED
            if st in (State.REJECTED, State.EXPIRED, State.CANCELED):
                return (
                    SendOutcome.ACCEPTED
                    if Decimal(str(body.get("executedQty", "0"))) > 0
                    else SendOutcome.REJECTED
                )
            return SendOutcome.UNKNOWN
        code = self._code(body)
        if code == -2010 and "duplicate" in str(body.get("msg", "")).lower():
            return SendOutcome.DUPLICATE_ID
        if code in (-2010, -1013, -2014, -2015) or (
            code is not None and -1199 <= code <= -1100 and code != -1121
        ):
            return SendOutcome.REJECTED  # explicit refusal (balance, filters, auth, malformed)
        return SendOutcome.UNKNOWN  # 429/418/5xx, -1021 clock, anything unrecognised

    def query(self, client_id: str) -> QueryResult:
        if not _CLIENT_ID.fullmatch(client_id):
            return QueryResult(QueryKind.UNAVAILABLE)
        # the symbol is not part of ExchangePort.query(); the venue needs it, so the caller binds it (see bind_symbol)
        sym = self._symbols.get(client_id)
        if sym is None:
            return QueryResult(QueryKind.UNAVAILABLE)
        res = self._call("GET", {"symbol": sym, "origClientOrderId": client_id})
        if res is None:
            return QueryResult(QueryKind.UNAVAILABLE)
        status, body = res
        if status == 200 and str(body.get("status")) in _STATUS:
            return QueryResult(
                QueryKind.FOUND, ExchangeView(client_id, _STATUS[str(body["status"])])
            )
        if self._code(body) == -2013:
            return QueryResult(QueryKind.NOT_FOUND)
        return QueryResult(QueryKind.UNAVAILABLE)

    def cancel(self, client_id: str) -> CancelResult:
        sym = self._symbols.get(client_id)
        if sym is None or not _CLIENT_ID.fullmatch(client_id):
            return CancelResult(CancelOutcome.UNKNOWN)
        res = self._call("DELETE", {"symbol": sym, "origClientOrderId": client_id})
        if res is None:
            return CancelResult(CancelOutcome.UNKNOWN)
        status, body = res
        if status == 200 and str(body.get("status")) == "CANCELED":
            return CancelResult(CancelOutcome.CANCELED)
        if self._code(body) == -2011:  # unknown order: never existed, or already final -> ask
            q = self.query(client_id)
            if q.kind == QueryKind.NOT_FOUND:
                return CancelResult(CancelOutcome.UNKNOWN_ORDER)
            if q.kind == QueryKind.FOUND and q.view and q.view.state in _TERMINAL:
                return CancelResult(CancelOutcome.FINAL, q.view.state)
        return CancelResult(CancelOutcome.UNKNOWN)

    # The venue addresses orders by (symbol, client id); the port remembers the symbol of every id it has sent so that
    # query/cancel can be called with the client id alone, as ExchangePort requires. Recovery after a restart must
    # re-bind from the durable intent store (Intent.symbol) before querying: see bind_symbol.

    def bind_symbol(self, client_id: str, symbol: str) -> None:
        self._symbols[client_id] = _venue_symbol(symbol)
