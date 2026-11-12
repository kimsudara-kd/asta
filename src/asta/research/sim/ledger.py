"""Immutable simulation ledger with weighted-average cost basis (ADR-0007).

Balances are a pure function of the entries. Errors are fixed by CORRECTION entries, never by
editing. Quantum 1e-8. Buy notional and fees round UP, sell notional rounds DOWN.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from asta.core.money import QUANTUM, quantize, round_down, round_up
from asta.research.data.profiles import FeeAssetPolicy

ZERO = Decimal(0)
BPS = Decimal(10_000)

KINDS = ("INITIAL_CASH", "RESERVE", "RELEASE", "FILL_BASE", "FILL_QUOTE", "FEE", "CORRECTION")


class LedgerError(Exception):
    """An invariant would be violated (negative balance, over-reservation, short sale...)."""


@dataclass(frozen=True)
class LedgerEntry:
    seq: int
    ts: datetime
    kind: str
    asset: str
    amount: Decimal
    order_id: int | None = None
    fill_id: int | None = None
    note: str = ""


@dataclass
class Position:
    qty: Decimal = ZERO
    cost_total: Decimal = ZERO  # quote cost of the open quantity (incl. capitalised quote fees)

    @property
    def avg_cost(self) -> Decimal | None:
        return None if self.qty == 0 else self.cost_total / self.qty


@dataclass(frozen=True)
class BuyResult:
    cash_out: Decimal
    received_qty: Decimal
    notional: Decimal
    fee_asset: str
    fee_amount: Decimal


@dataclass(frozen=True)
class SellResult:
    cash_in: Decimal
    notional: Decimal
    fee_amount: Decimal
    realized_pnl: Decimal
    cost_relieved: Decimal


@dataclass(frozen=True)
class ReconcileReport:
    ok: bool
    issues: tuple[str, ...]
    equity: Decimal
    realized: Decimal
    unrealized: Decimal


@dataclass
class Ledger:
    quote: str
    entries: list[LedgerEntry] = field(default_factory=list)
    _bal: dict[str, Decimal] = field(default_factory=dict)
    _res: dict[str, Decimal] = field(default_factory=dict)
    positions: dict[str, Position] = field(default_factory=dict)
    initial_cash: Decimal = ZERO
    realized_total: Decimal = ZERO
    realized_by_asset: dict[str, Decimal] = field(default_factory=dict)
    corrections_total: Decimal = ZERO
    fees_by_asset: dict[str, Decimal] = field(default_factory=dict)

    # ---- construction ----
    @staticmethod
    def open(quote: str, starting_cash: Decimal, ts: datetime) -> Ledger:
        if starting_cash <= 0:
            raise LedgerError("starting cash must be positive")
        led = Ledger(quote)
        led.initial_cash = quantize(starting_cash)
        led._add(ts, "INITIAL_CASH", quote, led.initial_cash)
        return led

    def _add(
        self,
        ts: datetime,
        kind: str,
        asset: str,
        amount: Decimal,
        order_id: int | None = None,
        fill_id: int | None = None,
        note: str = "",
    ) -> None:
        if kind not in KINDS:
            raise LedgerError(f"unknown entry kind {kind}")
        self.entries.append(
            LedgerEntry(len(self.entries) + 1, ts, kind, asset, amount, order_id, fill_id, note)
        )
        if kind == "RESERVE":
            self._res[asset] = self._res.get(asset, ZERO) + amount
        elif kind == "RELEASE":
            self._res[asset] = self._res.get(asset, ZERO) - amount
        else:
            self._bal[asset] = self._bal.get(asset, ZERO) + amount

    # ---- reads ----
    def balance(self, asset: str) -> Decimal:
        return self._bal.get(asset, ZERO)

    def reserved(self, asset: str) -> Decimal:
        return self._res.get(asset, ZERO)

    def available(self, asset: str) -> Decimal:
        return self.balance(asset) - self.reserved(asset)

    def position(self, base: str) -> Position:
        return self.positions.setdefault(base, Position())

    # ---- reservations ----
    def reserve(self, ts: datetime, asset: str, amount: Decimal, order_id: int) -> None:
        if amount <= 0:
            raise LedgerError("reservation must be positive")
        if amount > self.available(asset):
            raise LedgerError(
                f"insufficient available {asset}: need {amount}, have {self.available(asset)}"
            )
        self._add(ts, "RESERVE", asset, amount, order_id)

    def release(
        self, ts: datetime, asset: str, amount: Decimal, order_id: int, fill_id: int | None = None
    ) -> None:
        if amount <= 0:
            return
        if amount > self.reserved(asset):
            raise LedgerError(f"release {amount} exceeds reserved {self.reserved(asset)} {asset}")
        self._add(ts, "RELEASE", asset, amount, order_id, fill_id)

    # ---- fills ----
    def apply_buy(
        self,
        ts: datetime,
        base: str,
        qty: Decimal,
        price: Decimal,
        fee_bps: Decimal,
        policy: FeeAssetPolicy,
        order_id: int,
        fill_id: int,
    ) -> BuyResult:
        if qty <= 0 or price <= 0:
            raise LedgerError("buy needs positive qty and price")
        notional = round_up(price * qty)
        fee_base = fee_quote = ZERO
        if policy == FeeAssetPolicy.RECEIVED_ASSET:
            fee_base = round_up(qty * fee_bps / BPS)
        else:
            fee_quote = round_up(notional * fee_bps / BPS)
        received = qty - fee_base
        if received <= 0:
            raise LedgerError("fee consumes the whole fill")
        cash_out = notional + fee_quote
        if cash_out > self.balance(self.quote):
            raise LedgerError("negative cash would result")
        self._add(ts, "FILL_QUOTE", self.quote, -notional, order_id, fill_id)
        if fee_quote:
            self._add(ts, "FEE", self.quote, -fee_quote, order_id, fill_id, "buy fee (quote)")
            self.fees_by_asset[self.quote] = self.fees_by_asset.get(self.quote, ZERO) + fee_quote
        self._add(ts, "FILL_BASE", base, qty, order_id, fill_id)
        if fee_base:
            self._add(ts, "FEE", base, -fee_base, order_id, fill_id, "buy fee (received asset)")
            self.fees_by_asset[base] = self.fees_by_asset.get(base, ZERO) + fee_base
        pos = self.position(base)
        pos.qty += received
        pos.cost_total += cash_out
        return BuyResult(
            cash_out, received, notional, base if fee_base else self.quote, fee_base or fee_quote
        )

    def apply_sell(
        self,
        ts: datetime,
        base: str,
        qty: Decimal,
        price: Decimal,
        fee_bps: Decimal,
        order_id: int,
        fill_id: int,
    ) -> SellResult:
        pos = self.position(base)
        if qty <= 0 or price <= 0:
            raise LedgerError("sell needs positive qty and price")
        if qty > pos.qty or qty > self.balance(base):
            raise LedgerError("short sale / negative inventory refused")
        notional = round_down(price * qty)
        fee = round_up(notional * fee_bps / BPS)
        if fee > notional:
            raise LedgerError("fee exceeds proceeds")
        cash_in = notional - fee
        relieved = pos.cost_total if qty == pos.qty else quantize(pos.cost_total * qty / pos.qty)
        realized = cash_in - relieved
        self._add(ts, "FILL_BASE", base, -qty, order_id, fill_id)
        self._add(ts, "FILL_QUOTE", self.quote, notional, order_id, fill_id)
        if fee:
            self._add(ts, "FEE", self.quote, -fee, order_id, fill_id, "sell fee (quote)")
            self.fees_by_asset[self.quote] = self.fees_by_asset.get(self.quote, ZERO) + fee
        pos.qty -= qty
        pos.cost_total -= relieved
        if pos.qty == 0 and pos.cost_total != 0:
            raise LedgerError("cost basis must be zero when flat")
        self.realized_total += realized
        self.realized_by_asset[base] = self.realized_by_asset.get(base, ZERO) + realized
        return SellResult(cash_in, notional, fee, realized, relieved)

    def correct_cash(self, ts: datetime, amount: Decimal, reason: str) -> None:
        """Compensating entry (never an edit). Tracked so the equity identity stays exact."""
        if amount == 0:
            raise LedgerError("zero correction")
        if self.balance(self.quote) + amount < 0:
            raise LedgerError("correction would make cash negative")
        self._add(ts, "CORRECTION", self.quote, amount, note=reason)
        self.corrections_total += amount

    # ---- valuation ----
    def positions_value(self, marks: dict[str, Decimal]) -> Decimal:
        total = ZERO
        for base, pos in self.positions.items():
            if pos.qty:
                total += pos.qty * marks[base]
        return total

    def equity(self, marks: dict[str, Decimal]) -> Decimal:
        return self.balance(self.quote) + self.positions_value(marks)

    def unrealized(self, marks: dict[str, Decimal]) -> Decimal:
        return self.positions_value(marks) - sum(
            (p.cost_total for p in self.positions.values()), ZERO
        )

    # ---- reconciliation ----
    def recompute_balances(self) -> tuple[dict[str, Decimal], dict[str, Decimal]]:
        bal: dict[str, Decimal] = {}
        res: dict[str, Decimal] = {}
        for e in self.entries:
            if e.kind == "RESERVE":
                res[e.asset] = res.get(e.asset, ZERO) + e.amount
            elif e.kind == "RELEASE":
                res[e.asset] = res.get(e.asset, ZERO) - e.amount
            else:
                bal[e.asset] = bal.get(e.asset, ZERO) + e.amount
        return bal, res

    def reconcile(self, marks: dict[str, Decimal]) -> ReconcileReport:
        issues: list[str] = []
        bal, res = self.recompute_balances()
        for a in set(bal) | set(self._bal):
            if bal.get(a, ZERO) != self._bal.get(a, ZERO):
                issues.append(
                    f"balance drift {a}: entries={bal.get(a, ZERO)} state={self._bal.get(a, ZERO)}"
                )
        for a in set(res) | set(self._res):
            if res.get(a, ZERO) != self._res.get(a, ZERO):
                issues.append(f"reservation drift {a}")
        for a, v in bal.items():
            if v < 0:
                issues.append(f"negative balance {a}: {v}")
            if res.get(a, ZERO) < 0 or res.get(a, ZERO) > v:
                issues.append(
                    f"reservation out of range {a}: reserved={res.get(a, ZERO)} balance={v}"
                )
        for base, pos in self.positions.items():
            if pos.qty != bal.get(base, ZERO):
                issues.append(f"position qty != balance for {base}")
            if pos.qty < 0 or pos.cost_total < 0:
                issues.append(f"negative position {base}")
        equity = self.equity(marks)
        unreal = self.unrealized(marks)
        if equity - self.initial_cash != self.realized_total + unreal + self.corrections_total:
            issues.append(
                f"equity identity broken: {equity - self.initial_cash} != "
                f"{self.realized_total} + {unreal} + {self.corrections_total}"
            )
        return ReconcileReport(not issues, tuple(issues), equity, self.realized_total, unreal)


__all__ = [
    "QUANTUM",
    "BuyResult",
    "Ledger",
    "LedgerEntry",
    "LedgerError",
    "Position",
    "ReconcileReport",
    "SellResult",
]
