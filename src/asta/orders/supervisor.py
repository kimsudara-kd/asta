"""Glue between reconciliation, fill ingestion and the durable kill switch (pure over ports).

* ``ingest_fill``   - store one reported trade; a conflicting or impossible trade latches the halt.
* ``reconcile_cycle`` - compare our orders with the exchange's and halt on what cannot be explained:
  an order we never made, immediately; any other serious mismatch only when it persists for
  ``confirmations`` consecutive cycles (a fill that landed between two reads is a lag, not a fault).
The kill switch itself is durable (``SqlKillSwitch``) and is consulted by the dispatcher gate and by
a database trigger, so a trip here stops every worker's next dispatch, across restarts.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from asta.orders.fills import Fill
from asta.orders.lifecycle import Intent
from asta.orders.ports import ExchangeView
from asta.orders.reconcile import Finding, reconcile_balances, reconcile_orders
from asta.orders.sql_store import FillConflict, FillOutcome, FillRejected


class Tripper(Protocol):
    def trip(self, cause: str, actor: str = ..., reason: str = ...) -> None: ...


class FillSink(Protocol):
    def record_fill(self, decision_id: str, fill: Fill) -> FillOutcome: ...


class Ingest(StrEnum):
    STORED = "STORED"
    DUPLICATE = "DUPLICATE"
    HALTED = "HALTED"


@dataclass(frozen=True)
class IngestResult:
    status: Ingest
    detail: str = ""


def ingest_fill(sink: FillSink, kill: Tripper, decision_id: str, fill: Fill) -> IngestResult:
    try:
        out = sink.record_fill(decision_id, fill)
    except FillConflict as exc:
        kill.trip("FILL_CONFLICT", "fill-ingest", str(exc)[:200])
        return IngestResult(Ingest.HALTED, str(exc))
    except FillRejected as exc:
        kill.trip("FILL_REJECTED", "fill-ingest", f"{exc.kind}: {exc.detail}"[:200])
        return IngestResult(Ingest.HALTED, str(exc))
    return IngestResult(Ingest.STORED if out == FillOutcome.STORED else Ingest.DUPLICATE)


@dataclass
class CycleReport:
    findings: list[Finding] = field(default_factory=list)
    halted: bool = False
    skipped_exchange_unavailable: bool = False


@dataclass
class Reconciler:
    local: Callable[[], list[Intent]]
    exchange_orders: Callable[[], dict[str, ExchangeView] | None]
    kill: Tripper
    confirmations: int = 2
    _streak: dict[tuple[str, str], int] = field(default_factory=dict)

    def cycle(
        self,
        expected_balances: dict[str, Decimal] | None = None,
        actual_balances: dict[str, Decimal] | None = None,
    ) -> CycleReport:
        rep = CycleReport()
        views = self.exchange_orders()
        if views is None:  # cannot ask: learn nothing, change nothing
            rep.skipped_exchange_unavailable = True
            return rep
        found = reconcile_orders(self.local(), views)
        if expected_balances is not None and actual_balances is not None:
            found += reconcile_balances(expected_balances, actual_balances)
        rep.findings = found
        seen = {(f.code, f.subject) for f in found if f.serious}
        for key in list(self._streak):
            if key not in seen:
                del self._streak[key]  # it went away: the lag was real
        for f in found:
            if not f.serious:
                continue
            key = (f.code, f.subject)
            self._streak[key] = self._streak.get(key, 0) + 1
            foreign = f.code == "UNKNOWN_AT_EXCHANGE"
            if foreign or self._streak[key] >= self.confirmations:
                cause = "UNKNOWN_EXCHANGE_ORDER" if foreign else "RECONCILIATION_MISMATCH"
                self.kill.trip(cause, "reconciler", f"{f.code} {f.subject}: {f.detail}"[:200])
                rep.halted = True
        return rep
