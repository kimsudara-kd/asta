"""Paper-session configuration (M2). Frozen for the life of a session; every assumption is saved
with it and hashed. Pure: no I/O, no network.

A session owns one virtual wallet per venue. Each (venue, strategy) pair trades its own slice of
that wallet in a separate simulated account; portfolio equity and risk are judged globally.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from asta.core.money import QUANTUM, round_down
from asta.research.data.profiles import FeeSchedule, RuleProfile
from asta.research.runner import canonical
from asta.research.sim.execution import ExecutionModel
from asta.research.sim.risk import SimRiskLimits
from asta.research.strategies.registry import REGISTRY, default_params_json
from asta.research.types import Timeframe

CONFIG_VERSION = "paper-cfg-1"
LABEL = "PAPER TRADING — VIRTUAL FUNDS ONLY"
ZERO = Decimal(0)

ROLE_ACTIVE = "ACTIVE"
ROLE_BENCH_CONSTRAINED = "BENCHMARK_CONSTRAINED"
ROLE_BENCH_UNCONSTRAINED = "BENCHMARK_UNCONSTRAINED"
#: Benchmarks are shadow accounts: labelled, never part of portfolio equity or global risk.
BENCHMARK_ROLES = (ROLE_BENCH_CONSTRAINED, ROLE_BENCH_UNCONSTRAINED)


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    params: dict[str, Any] = field(default_factory=dict)
    weight: Decimal = Decimal(1)

    def resolved_params(self, symbols: tuple[str, ...]) -> dict[str, Any]:
        p = default_params_json(self.strategy_id)
        if "symbols" in p:
            p["symbols"] = list(symbols)
        p.update(self.params)
        return p

    def to_json(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "version": REGISTRY[self.strategy_id].version,
            "params": self.params,
            "weight": str(self.weight),
            # M1 established no validated profitable, risk-compliant edge: nothing is a candidate.
            "deployment_candidate": False,
            "research_status": "NO_VALIDATED_EDGE (M1)",
        }

    @staticmethod
    def from_json(d: dict[str, Any]) -> StrategySpec:
        return StrategySpec(str(d["strategy_id"]), dict(d["params"]), Decimal(str(d["weight"])))


@dataclass(frozen=True)
class AccountSpec:
    key: str  # "BINANCE:regime_grid" | "BENCH:hold_constrained"
    venue: str  # data/rules venue
    strategy_id: str
    params: dict[str, Any]
    starting_cash: Decimal
    role: str
    counts_toward_portfolio: bool


@dataclass(frozen=True)
class PaperSessionConfig:
    name: str
    wallets: dict[str, Decimal]  # venue -> virtual USDT
    enabled_venues: tuple[str, ...]  # venues with a verified feed; the rest keep cash uninvested
    strategies: tuple[StrategySpec, ...]
    rules: dict[str, tuple[RuleProfile, ...]]  # venue -> rule profiles (all session symbols)
    fees: dict[str, FeeSchedule]  # venue -> fee profile
    execution: ExecutionModel = field(default_factory=ExecutionModel)
    symbols: tuple[str, ...] = ("BTC-USDT", "ETH-USDT", "SOL-USDT")
    timeframe: Timeframe = Timeframe.H1
    quote_asset: str = "USDT"
    warmup_bars: int = 300
    latency_bars: int = 0
    risk_version_label: str = "reference-policy-m1"
    risk: SimRiskLimits | None = None  # None => accepted M1 reference policy on total wallets
    benchmarks: bool = True
    benchmark_venue: str | None = None
    stale_after_seconds: int = 180  # no complete 1m candle for this long => entries blocked
    checkpoint_every: int = 1
    #: opt-in (default False): drop repeated identical headroom rejections (repeat_guard.py). Part of the
    #: config (and its hash) ONLY when True, so every existing session's hash and replay are unchanged.
    suppress_repeated_rejections: bool = False

    # ---------------------------------------------------------------- derived
    @property
    def total_equity(self) -> Decimal:
        return sum(self.wallets.values(), ZERO)

    @property
    def risk_limits(self) -> SimRiskLimits:
        return self.risk or SimRiskLimits.default(self.total_equity)

    def validate(self) -> None:
        if not self.wallets or any(v <= 0 for v in self.wallets.values()):
            raise ConfigError("every venue wallet must be a positive virtual amount")
        if set(self.enabled_venues) - set(self.wallets):
            raise ConfigError("enabled venue without a wallet")
        if not self.strategies:
            raise ConfigError("at least one strategy is required")
        for s in self.strategies:
            if s.strategy_id not in REGISTRY:
                raise ConfigError(f"unknown strategy {s.strategy_id!r}")
            if s.strategy_id in ("passive_hold", "all_cash"):
                raise ConfigError("passive benchmarks are shadow accounts, not active strategies")
            if s.weight <= 0:
                raise ConfigError("strategy weight must be positive")
        if self.risk is not None and self.risk.starting_equity != self.total_equity:
            raise ConfigError("risk starting equity must equal the total virtual wallets")
        if self.risk is not None and self.risk.label != "RISK_COMPLIANT":
            raise ConfigError("paper sessions require a risk-compliant policy")
        for v in self.enabled_venues:
            if v not in self.fees:
                raise ConfigError(f"fee profile missing for {v}")
            have = {r.symbol for r in self.rules.get(v, ())}
            if not set(self.symbols) <= have:
                raise ConfigError(f"trading rules missing for {v}: {set(self.symbols) - have}")
        if self.timeframe not in (Timeframe.M5, Timeframe.M15, Timeframe.H1):
            raise ConfigError("session timeframe must be 5m, 15m or 1h")

    def accounts(self) -> tuple[AccountSpec, ...]:
        """Deterministic account list: active (venue x strategy) slices, then shadow benchmarks."""
        out: list[AccountSpec] = []
        wsum = sum((s.weight for s in self.strategies), ZERO)
        for venue in self.enabled_venues:
            wallet = self.wallets[venue]
            slices = [round_down(wallet * s.weight / wsum, QUANTUM) for s in self.strategies]
            slices[0] += wallet - sum(slices, ZERO)  # rounding remainder: nothing is lost
            for s, cash in zip(self.strategies, slices, strict=True):
                out.append(
                    AccountSpec(
                        f"{venue}:{s.strategy_id}",
                        venue,
                        s.strategy_id,
                        s.resolved_params(self.symbols),
                        cash,
                        ROLE_ACTIVE,
                        True,
                    )
                )
        if self.benchmarks and self.enabled_venues:
            bv = self.benchmark_venue or self.enabled_venues[0]
            out.append(
                AccountSpec(
                    "BENCH:hold_constrained",
                    bv,
                    "passive_hold",
                    {"symbols": list(self.symbols), "notional_per_symbol": "100"},
                    self.total_equity,
                    ROLE_BENCH_CONSTRAINED,
                    False,
                )
            )
            out.append(
                AccountSpec(
                    "BENCH:hold_unconstrained",
                    bv,
                    "passive_hold",
                    {"symbols": list(self.symbols), "notional_per_symbol": "333"},
                    self.total_equity,
                    ROLE_BENCH_UNCONSTRAINED,
                    False,
                )
            )
        return tuple(out)

    # ---------------------------------------------------------------- serialisation
    def to_json(self) -> dict[str, Any]:
        out = self._base_json()
        if self.suppress_repeated_rejections:
            out["suppress_repeated_rejections"] = True
        return out

    def _base_json(self) -> dict[str, Any]:
        return {
            "config_version": CONFIG_VERSION,
            "label": LABEL,
            "name": self.name,
            "wallets": {k: str(v) for k, v in sorted(self.wallets.items())},
            "enabled_venues": list(self.enabled_venues),
            "disabled_venues_cash_uninvested": sorted(set(self.wallets) - set(self.enabled_venues)),
            "strategies": [s.to_json() for s in self.strategies],
            "rules": {
                v: [r.to_json() for r in sorted(rs, key=lambda r: r.symbol)]
                for v, rs in sorted(self.rules.items())
            },
            "fees": {v: f.to_json() for v, f in sorted(self.fees.items())},
            "execution": self.execution.to_json(),
            "symbols": list(self.symbols),
            "timeframe": self.timeframe.value,
            "quote_asset": self.quote_asset,
            "warmup_bars": self.warmup_bars,
            "latency_bars": self.latency_bars,
            "risk": self.risk_limits.to_json(),
            "risk_version_label": self.risk_version_label,
            "risk_daily_auto_reset": False,
            "benchmarks": self.benchmarks,
            "benchmark_venue": self.benchmark_venue,
            "stale_after_seconds": self.stale_after_seconds,
            "checkpoint_every": self.checkpoint_every,
        }

    @staticmethod
    def from_json(d: dict[str, Any]) -> PaperSessionConfig:
        return PaperSessionConfig(
            name=str(d["name"]),
            wallets={k: Decimal(str(v)) for k, v in d["wallets"].items()},
            enabled_venues=tuple(d["enabled_venues"]),
            strategies=tuple(StrategySpec.from_json(s) for s in d["strategies"]),
            rules={v: tuple(RuleProfile.from_json(r) for r in rs) for v, rs in d["rules"].items()},
            fees={v: FeeSchedule.from_json(f) for v, f in d["fees"].items()},
            execution=ExecutionModel.from_json(d["execution"]),
            symbols=tuple(d["symbols"]),
            timeframe=Timeframe(d["timeframe"]),
            quote_asset=str(d["quote_asset"]),
            warmup_bars=int(d["warmup_bars"]),
            latency_bars=int(d["latency_bars"]),
            risk_version_label=str(d["risk_version_label"]),
            risk=SimRiskLimits.from_json(d["risk"]),
            benchmarks=bool(d["benchmarks"]),
            benchmark_venue=d.get("benchmark_venue"),
            stale_after_seconds=int(d["stale_after_seconds"]),
            checkpoint_every=int(d["checkpoint_every"]),
            suppress_repeated_rejections=bool(d.get("suppress_repeated_rejections", False)),
        )

    def config_hash(self) -> str:
        return hashlib.sha256(canonical(self.to_json()).encode()).hexdigest()
