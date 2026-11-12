"""Strategy registry: id -> class + parameter parsing from JSON (so a run is fully reproducible from
its persisted configuration)."""

from __future__ import annotations

import dataclasses
import typing
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from asta.research.strategies.base import Strategy, params_to_json
from asta.research.strategies.baselines import (
    AllCash,
    AllCashParams,
    PassiveHold,
    PassiveHoldParams,
    SmaCross,
    SmaCrossParams,
)
from asta.research.strategies.grid import GridParams, RegimeGrid
from asta.research.strategies.trend_pullback import TrendPullback, TrendPullbackParams


@dataclass(frozen=True)
class StrategyInfo:
    strategy_id: str
    version: str
    display_name: str
    kind: str  # STRATEGY | BASELINE
    description: str
    cls: type[Strategy]
    params_cls: type[Any]
    risk_profile: str = "RISK_COMPLIANT"  # or RISK_UNCONSTRAINED_REFERENCE


# Display names avoid trading verbs on purpose (the UI must not look like an execution surface).
REGISTRY: dict[str, StrategyInfo] = {
    i.strategy_id: i
    for i in (
        StrategyInfo(
            "regime_grid",
            "1.0.0",
            "Regime-aware spot grid",
            "STRATEGY",
            "Resting limit ladder inside a range; suspended in downtrends/high volatility; risk-off below the range.",
            RegimeGrid,
            GridParams,
        ),
        StrategyInfo(
            "trend_pullback",
            "1.0.0",
            "Trend pullback",
            "STRATEGY",
            "Uptrend + pullback entry; take-profit/stop pair, time stop, cool-down.",
            TrendPullback,
            TrendPullbackParams,
        ),
        StrategyInfo(
            "passive_hold",
            "1.0.0",
            "Passive hold (baseline)",
            "BASELINE",
            "Invests once at the first bar and holds; equal notional per symbol.",
            PassiveHold,
            PassiveHoldParams,
        ),
        StrategyInfo(
            "all_cash",
            "1.0.0",
            "All cash (baseline)",
            "BASELINE",
            "Never trades; equity stays at the starting cash.",
            AllCash,
            AllCashParams,
        ),
        StrategyInfo(
            "sma_cross",
            "1.0.0",
            "Moving-average rules (baseline)",
            "BASELINE",
            "Long while SMA(fast) > SMA(slow); otherwise flat.",
            SmaCross,
            SmaCrossParams,
        ),
    )
}


def parse_params(params_cls: type[Any], raw: dict[str, Any]) -> Any:
    """Build a params dataclass from JSON, converting numbers-as-strings to Decimal, lists to
    tuples. Unknown keys are rejected (a typo must not silently change a research run)."""
    hints = typing.get_type_hints(params_cls)
    fields = {f.name for f in dataclasses.fields(params_cls)}
    unknown = set(raw) - fields
    if unknown:
        raise ValueError(f"unknown parameters: {sorted(unknown)}")
    kwargs: dict[str, Any] = {}
    for name, value in raw.items():
        kwargs[name] = _convert(hints[name], value)
    return params_cls(**kwargs)


def _convert(hint: Any, value: Any) -> Any:
    origin = typing.get_origin(hint)
    if hint is Decimal:
        return Decimal(str(value))
    if hint is int:
        if isinstance(value, bool) or int(value) != value:
            raise ValueError("expected an integer")
        return int(value)
    if hint is bool:
        return bool(value)
    if hint is str:
        return str(value)
    if origin is tuple:
        args = typing.get_args(hint)
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_convert(args[0], v) for v in value)
        return tuple(_convert(a, v) for a, v in zip(args, value, strict=True))
    return value


def build_strategy(strategy_id: str, params: dict[str, Any] | None = None) -> Strategy:
    info = REGISTRY.get(strategy_id)
    if info is None:
        raise ValueError(f"unknown strategy {strategy_id!r}")
    parsed = parse_params(info.params_cls, params or {})
    return info.cls(parsed)  # type: ignore[call-arg]


def default_params_json(strategy_id: str) -> dict[str, Any]:
    info = REGISTRY[strategy_id]
    return params_to_json(info.params_cls())
