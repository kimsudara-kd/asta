"""Risk policy foundation (handoff §8): pure, deterministic, fail-closed checks.

These are evaluated for display and for later milestones; in M0 the gateway denies regardless.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class RiskPolicy:
    starting_equity: Decimal = Decimal("1000")
    max_invested_pct: Decimal = Decimal("0.30")
    max_per_asset_pct: Decimal = Decimal("0.10")
    min_cash_reserve_pct: Decimal = Decimal("0.70")
    daily_drawdown_trip_pct: Decimal = Decimal("0.02")
    peak_drawdown_trip_pct: Decimal = Decimal("0.05")
    max_positions: int = 3
    min_net_edge_pct: Decimal = Decimal("0.0020")

    @property
    def max_invested(self) -> Decimal:
        return self.starting_equity * self.max_invested_pct

    @property
    def max_per_asset(self) -> Decimal:
        return self.starting_equity * self.max_per_asset_pct


@dataclass(frozen=True)
class RiskSnapshot:
    equity: Decimal
    start_of_day_equity: Decimal
    peak_equity: Decimal
    invested_by_asset: dict[str, Decimal]
    cash: Decimal
    data_fresh: bool
    clock_ok: bool
    reconciled: bool


@dataclass(frozen=True)
class RiskEvaluation:
    halt_new_buys: bool
    reasons: tuple[str, ...]


def evaluate(policy: RiskPolicy, snap: RiskSnapshot) -> RiskEvaluation:
    """Any breach, or any unknown/unsafe input, halts new buys and exposure increases."""
    reasons: list[str] = []
    if not snap.data_fresh:
        reasons.append("STALE_MARKET_DATA")
    if not snap.clock_ok:
        reasons.append("CLOCK_DRIFT")
    if not snap.reconciled:
        reasons.append("UNRECONCILED")
    if snap.start_of_day_equity > 0 and (
        (snap.start_of_day_equity - snap.equity) / snap.start_of_day_equity
        >= policy.daily_drawdown_trip_pct
    ):
        reasons.append("DAILY_DRAWDOWN_TRIP")
    if snap.peak_equity > 0 and (
        (snap.peak_equity - snap.equity) / snap.peak_equity >= policy.peak_drawdown_trip_pct
    ):
        reasons.append("PEAK_DRAWDOWN_TRIP")
    invested = sum(snap.invested_by_asset.values(), Decimal(0))
    if invested > policy.max_invested:
        reasons.append("TOTAL_EXPOSURE_CAP")
    if any(v > policy.max_per_asset for v in snap.invested_by_asset.values()):
        reasons.append("ASSET_EXPOSURE_CAP")
    if len([v for v in snap.invested_by_asset.values() if v > 0]) > policy.max_positions:
        reasons.append("MAX_POSITIONS")
    if snap.equity > 0 and snap.cash / snap.equity < policy.min_cash_reserve_pct:
        reasons.append("CASH_RESERVE")
    if snap.cash < 0 or any(v < 0 for v in snap.invested_by_asset.values()):
        reasons.append("NEGATIVE_BALANCE_INVARIANT")
    return RiskEvaluation(halt_new_buys=bool(reasons), reasons=tuple(reasons))
