"""In-process MOCK of the Binance Spot Testnet order endpoints, for offline connector tests. Not an exchange client:
it only answers requests handed to it as a Transport callable (no sockets). It verifies the Ed25519 signature,
timestamp window and replay exactly as ``mock_endpoint_verify`` does, keeps orders by client id, and can inject the
faults that matter for exactly-once behaviour (timeout before/after processing, 5xx after processing, rate limits)."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlsplit

from signer import SignedRequest, mock_endpoint_verify


class MockTestnetExchange:
    def __init__(self, public_key_b64: str, now_ms: Callable[[], int]) -> None:
        self._pub, self._now = public_key_b64, now_ms
        self.orders: dict[str, dict[str, Any]] = {}
        self.requests: list[tuple[str, str]] = []  # (method, path) only: never URLs/signatures
        self.faults: list[str] = []  # consumed one per request
        self._seen: set[str] = set()
        self.reject_with: tuple[int, str] | None = None  # next POST is refused with (code, msg)
        self.trade_log: list[dict[str, Any]] = []
        self.balances: dict[str, Decimal] = {"USDT": Decimal("10000"), "BTC": Decimal(0)}
        self._order_seq, self._trade_seq = 1000, 5000
        self.fee_rate = Decimal("0.001")

    # test helpers --------------------------------------------------------------------------------
    def _record_trade(self, o: dict[str, Any], qty: Decimal, price: Decimal) -> None:
        """One trade: BUY adds base, spends quote; the fee is charged in the base asset (as the venue does for buys)."""
        base, quote = o["symbol"][:-4], o["symbol"][-4:]
        fee = (qty * self.fee_rate).quantize(Decimal("0.00000001"))
        self._trade_seq += 1
        self.trade_log.append(
            {"id": self._trade_seq, "orderId": o["orderId"], "symbol": o["symbol"], "price": str(price),
             "qty": str(qty), "commission": str(fee), "commissionAsset": base, "isBuyer": o["side"] == "BUY"}
        )  # fmt: skip
        sign = Decimal(1) if o["side"] == "BUY" else Decimal(-1)
        self.balances[base] = self.balances.get(base, Decimal(0)) + sign * qty - fee
        self.balances[quote] = self.balances.get(quote, Decimal(0)) - sign * qty * price

    def fill(
        self,
        client_id: str,
        qty: Decimal | None = None,
        price: Decimal = Decimal("50000"),
        trades: int = 1,
    ) -> None:
        o = self.orders[client_id]
        full = Decimal(o["origQty"])
        already = Decimal(o["executedQty"])
        add = qty if qty is not None else full - already
        step = (add / trades).quantize(Decimal("0.00000001"))
        for i in range(trades):
            self._record_trade(o, step if i < trades - 1 else add - step * (trades - 1), price)
        o["executedQty"] = str(already + add)
        o["status"] = "FILLED" if Decimal(o["executedQty"]) >= full else "PARTIALLY_FILLED"

    # the Transport ------------------------------------------------------------------------------
    def __call__(self, req: SignedRequest, timeout_s: float) -> tuple[int, dict[str, Any]]:
        fault = self.faults.pop(0) if self.faults else ""
        if fault == "timeout_before":
            raise TimeoutError
        if fault == "429":
            return 429, {"code": -1003, "msg": "Too many requests"}
        ok, why = mock_endpoint_verify(self._pub, req.url, self._now(), self._seen)
        if not ok:
            if why == "OUTSIDE_RECV_WINDOW":
                return 400, {"code": -1021, "msg": "Timestamp outside of the recvWindow"}
            return 401, {"code": -1022, "msg": f"Signature invalid ({why})"}
        if "X-MBX-APIKEY" not in req.headers:
            return 401, {"code": -2015, "msg": "Invalid API-key"}
        parts = urlsplit(req.url)
        self.requests.append((req.method, parts.path))
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        status, body = self._handle(req.method, q, parts.path)
        if fault == "timeout_after":
            raise TimeoutError  # processed, but the caller never learns
        if fault == "503_after":
            return 503, {
                "code": -1007,
                "msg": "Timeout waiting for response from backend server. Send status unknown",
            }
        return status, body

    def _view(self, o: dict[str, Any]) -> dict[str, Any]:
        return {
            k: o[k]
            for k in (
                "symbol",
                "clientOrderId",
                "status",
                "executedQty",
                "origQty",
                "side",
                "type",
                "orderId",
            )
        }

    def _handle(
        self, method: str, q: dict[str, str], path: str = "/api/v3/order"
    ) -> tuple[int, dict[str, Any]]:
        if path == "/api/v3/myTrades" and method == "GET":
            oid = int(q.get("orderId", "0"))
            rows = [
                dict(x)
                for x in self.trade_log
                if x["orderId"] == oid and x["symbol"] == q.get("symbol")
            ]
            return 200, {"value": rows}  # the real transport wraps a JSON list the same way
        if path == "/api/v3/openOrders" and method == "GET":
            rows = [
                self._view(o)
                for o in self.orders.values()
                if o["symbol"] == q.get("symbol") and o["status"] in ("NEW", "PARTIALLY_FILLED")
            ]
            return 200, {"value": rows}
        if path == "/api/v3/account" and method == "GET":
            rows = [
                {"asset": a, "free": format(v, "f"), "locked": "0.00000000"}
                for a, v in self.balances.items()
            ]
            if q.get("omitZeroBalances") == "true":
                rows = [r for r in rows if Decimal(r["free"]) != 0]
            return 200, {"canTrade": True, "balances": rows}
        cid = q.get("newClientOrderId") or q.get("origClientOrderId", "")
        if method == "POST":
            if self.reject_with:
                code, msg = self.reject_with
                self.reject_with = None
                return 400, {"code": code, "msg": msg}
            if cid in self.orders:
                return 400, {"code": -2010, "msg": "Duplicate order sent."}
            qty = q.get("quantity") or q.get("quoteOrderQty") or "0"
            self._order_seq += 1
            if (
                q["type"] == "MARKET" and "quoteOrderQty" in q
            ):  # a quote-sized market buy fills at the mock price
                qty = str(
                    (Decimal(q["quoteOrderQty"]) / Decimal("50000")).quantize(Decimal("0.00000001"))
                )
            o = {"symbol": q["symbol"], "clientOrderId": cid, "side": q["side"], "type": q["type"],
                 "origQty": qty, "executedQty": "0", "status": "NEW", "orderId": self._order_seq}  # fmt: skip
            self.orders[cid] = o
            if q["type"] == "MARKET":
                self.fill(cid)
            return 200, self._view(o)
        o = self.orders.get(cid)
        if method == "GET":
            return (
                (200, self._view(o))
                if o
                else (400, {"code": -2013, "msg": "Order does not exist."})
            )
        if method == "DELETE":
            if not o or o["status"] not in ("NEW", "PARTIALLY_FILLED"):
                return 400, {"code": -2011, "msg": "Unknown order sent."}
            o["status"] = "CANCELED"
            return 200, self._view(o)
        return 405, {"code": -1, "msg": "method"}
