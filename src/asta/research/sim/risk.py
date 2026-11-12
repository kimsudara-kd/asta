"""Simulation risk engine (ADR-0007). Limits are immutable for the life of a run.

Bases (explicit): per-asset and total exposure caps use STARTING equity; the daily-loss breaker
uses the UTC-session-start equity; the drawdown halt uses PEAK equity. All equity is
mark-to-market including unrealized P&L. Breakers are evaluated on bar-close equity (intrabar
excursions are not observable in candle data - a documented limitation).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from asta.risk.policy import RiskPolicy

RISK_POLICY_VERSION = "sim-risk-1"


@dataclass(frozen=True)
class SimRiskLimits:
    starting_equity: Decimal
    max_asset_pct: Decimal  # of starting equity
    max_total_pct: Decimal  # of starting equity
    daily_loss_pct: Decimal | None  # of session-start equity; None = breaker disabled
    max_drawdown_pct: Decimal | None  # of peak equity; None = halt disabled
    label: str = "RISK_COMPLIANT"
    version: str = RISK_POLICY_VERSION

    @staticmethod
    def default(starting_equity: Decimal = Decimal("1000")) -> SimRiskLimits:
        """Defaults come from the M0 RiskPolicy (no duplicated numbers)."""
        p = RiskPolicy(starting_equity=starting_equity)
        return SimRiskLimits(
            starting_equity,
            p.max_per_asset_pct,
            p.max_invested_pct,
            p.daily_drawdown_trip_pct,
            p.peak_drawdown_trip_pct,
        )

    @staticmethod
    def unconstrained_reference(starting_equity: Decimal = Decimal("1000")) -> SimRiskLimits:
        """For labelled reference baselines only (e.g. full-capital hold). NOT risk-compliant."""
        return SimRiskLimits(
            starting_equity, Decimal(1), Decimal(1), None, None, "RISK_UNCONSTRAINED_REFERENCE"
        )

    @property
    def max_asset(self) -> Decimal:
        return self.starting_equity * self.max_asset_pct

    @property
    def max_total(self) -> Decimal:
        return self.starting_equity * self.max_total_pct

    def to_json(self) -> dict[str, Any]:
        return {
            "starting_equity": str(self.starting_equity),
            "max_asset_pct": str(self.max_asset_pct),
            "max_total_pct": str(self.max_total_pct),
            "daily_loss_pct": None if self.daily_loss_pct is None else str(self.daily_loss_pct),
            "max_drawdown_pct": None
            if self.max_drawdown_pct is None
            else str(self.max_drawdown_pct),
            "label": self.label,
            "version": self.version,
        }

    @staticmethod
    def from_json(d: dict[str, Any]) -> SimRiskLimits:
        def opt(v: Any) -> Decimal | None:
            return None if v is None else Decimal(str(v))

        return SimRiskLimits(
            Decimal(str(d["starting_equity"])),
            Decimal(str(d["max_asset_pct"])),
            Decimal(str(d["max_total_pct"])),
            opt(d["daily_loss_pct"]),
            opt(d["max_drawdown_pct"]),
            str(d["label"]),
            str(d["version"]),
        )


@dataclass(frozen=True)
class RiskEvent:
    seq: int
    ts: datetime
    kind: str  # DAILY_LOSS_TRIP | DRAWDOWN_HALT | DAILY_RESET | ORDER_REJECTED
    detail: str
    equity: Decimal


@dataclass
class RiskState:
    peak_equity: Decimal
    session_day: date | None = None
    session_start_equity: Decimal = Decimal(0)
    daily_tripped: bool = False
    drawdown_halted: bool = False
    events: list[RiskEvent] = field(default_factory=list)

    @property
    def halted_new_buys(self) -> bool:
        return self.daily_tripped or self.drawdown_halted


class RiskEngine:
    def __init__(self, limits: SimRiskLimits) -> None:
        self.limits = limits
        self.state = RiskState(peak_equity=limits.starting_equity)

    def _event(self, ts: datetime, kind: str, detail: str, equity: Decimal) -> None:
        self.state.events.append(RiskEvent(len(self.state.events) + 1, ts, kind, detail, equity))

    def on_equity(self, ts: datetime, equity: Decimal) -> list[str]:
        """Update running state; returns the NEW trips caused by this observation."""
        st, lim = self.state, self.limits
        new: list[str] = []
        day = ts.date()
        if st.session_day != day:
            if st.daily_tripped:
                self._event(ts, "DAILY_RESET", "new UTC session; daily breaker re-armed", equity)
            st.session_day = day
            st.session_start_equity = equity
            st.daily_tripped = False
        if equity > st.peak_equity:
            st.peak_equity = equity
        if (
            lim.daily_loss_pct is not None
            and not st.daily_tripped
            and st.session_start_equity > 0
            and (st.session_start_equity - equity) / st.session_start_equity >= lim.daily_loss_pct
        ):
            st.daily_tripped = True
            self._event(
                ts,
                "DAILY_LOSS_TRIP",
                f"equity {equity} vs session start {st.session_start_equity}",
                equity,
            )
            new.append("DAILY_LOSS_TRIP")
        if (
            lim.max_drawdown_pct is not None
            and not st.drawdown_halted
            and st.peak_equity > 0
            and (st.peak_equity - equity) / st.peak_equity >= lim.max_drawdown_pct
        ):
            st.drawdown_halted = True
            self._event(ts, "DRAWDOWN_HALT", f"equity {equity} vs peak {st.peak_equity}", equity)
            new.append("DRAWDOWN_HALT")
        return new

    def check_buy(self, asset_exposure_after: Decimal, total_exposure_after: Decimal) -> str | None:
        """Reject reason for a risk-increasing order, else None. Sells are never blocked."""
        if self.state.drawdown_halted:
            return "RISK_DRAWDOWN_HALT"
        if self.state.daily_tripped:
            return "RISK_DAILY_LOSS_TRIP"
        if asset_exposure_after > self.limits.max_asset:
            return "RISK_ASSET_EXPOSURE_CAP"
        if total_exposure_after > self.limits.max_total:
            return "RISK_TOTAL_EXPOSURE_CAP"
        return None

    def record_rejection(self, ts: datetime, reason: str, equity: Decimal) -> None:
        self._event(ts, "ORDER_REJECTED", reason, equity)
