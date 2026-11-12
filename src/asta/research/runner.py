"""Pure backtest runner: frozen `BacktestConfig` + immutable datasets -> `BacktestRun`.

A run is reproducible from its persisted configuration and the content-addressed input data: the
config hash and result checksum below let anyone verify that. No I/O, no network, no database.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from asta.research.analytics.metrics import PerformanceReport, compute_performance
from asta.research.data.checksum import canon_decimal
from asta.research.data.models import Bar, Dataset
from asta.research.data.profiles import FeeSchedule, RuleProfile
from asta.research.sim.engine import Engine, EngineConfig, RunResult
from asta.research.sim.execution import ExecutionModel
from asta.research.sim.risk import SimRiskLimits
from asta.research.strategies.registry import REGISTRY, build_strategy
from asta.research.types import NON_RESEARCH_SOURCES, RESULT_LABELS, Severity, SourceType, Timeframe

RUNNER_VERSION = "runner-1"


class RunRefused(Exception):
    """The run was not started because an input is not acceptable (e.g. a REJECTED dataset)."""


@dataclass(frozen=True)
class BacktestConfig:
    strategy_id: str
    strategy_params: dict[str, Any]
    dataset_checksums: dict[str, str]  # symbol -> dataset content checksum (the input version)
    timeframe: Timeframe
    rules: tuple[RuleProfile, ...]
    fees: FeeSchedule
    execution: ExecutionModel
    risk: SimRiskLimits
    starting_cash: Decimal = Decimal(1000)
    quote_asset: str = "USDT"
    latency_bars: int = 0
    period_start: datetime | None = None  # first bar at which trading is allowed
    period_end: datetime | None = None  # exclusive
    warmup_bars: int = 0  # bars BEFORE period_start fed to indicators only
    seed: int = 0
    checkpoint_every: int = 500
    label: str = ""

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self.dataset_checksums))

    def to_json(self) -> dict[str, Any]:
        return {
            "runner_version": RUNNER_VERSION,
            "strategy_id": self.strategy_id,
            "strategy_version": REGISTRY[self.strategy_id].version,
            "strategy_params": self.strategy_params,
            "dataset_checksums": dict(sorted(self.dataset_checksums.items())),
            "timeframe": self.timeframe.value,
            "rules": [r.to_json() for r in sorted(self.rules, key=lambda r: r.symbol)],
            "fees": self.fees.to_json(),
            "execution": self.execution.to_json(),
            "risk": self.risk.to_json(),
            "starting_cash": str(self.starting_cash),
            "quote_asset": self.quote_asset,
            "latency_bars": self.latency_bars,
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "warmup_bars": self.warmup_bars,
            "seed": self.seed,
            "checkpoint_every": self.checkpoint_every,
            "label": self.label,
        }

    @staticmethod
    def from_json(d: dict[str, Any]) -> BacktestConfig:
        def ts(v: Any) -> datetime | None:
            return None if v is None else datetime.fromisoformat(v)

        return BacktestConfig(
            strategy_id=d["strategy_id"],
            strategy_params=dict(d["strategy_params"]),
            dataset_checksums=dict(d["dataset_checksums"]),
            timeframe=Timeframe(d["timeframe"]),
            rules=tuple(RuleProfile.from_json(r) for r in d["rules"]),
            fees=FeeSchedule.from_json(d["fees"]),
            execution=ExecutionModel.from_json(d["execution"]),
            risk=SimRiskLimits.from_json(d["risk"]),
            starting_cash=Decimal(str(d["starting_cash"])),
            quote_asset=d["quote_asset"],
            latency_bars=int(d["latency_bars"]),
            period_start=ts(d["period_start"]),
            period_end=ts(d["period_end"]),
            warmup_bars=int(d["warmup_bars"]),
            seed=int(d["seed"]),
            checkpoint_every=int(d["checkpoint_every"]),
            label=d.get("label", ""),
        )

    def config_hash(self) -> str:
        return hashlib.sha256(canonical(self.to_json()).encode()).hexdigest()

    def replace(self, **kw: Any) -> BacktestConfig:
        import dataclasses

        return dataclasses.replace(self, **kw)


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=_default)


def _default(o: Any) -> Any:
    if isinstance(o, Decimal):
        # value-based text: 100.10 and 100.1 are the same number and must hash the same
        return canon_decimal(o)
    if isinstance(o, datetime):
        return o.isoformat()
    if hasattr(o, "value") and not isinstance(o, type):
        return o.value
    raise TypeError(f"not JSON serialisable: {type(o)}")


@dataclass
class BacktestRun:
    config: BacktestConfig
    config_hash: str
    result: RunResult
    performance: PerformanceReport
    result_checksum: str
    data_labels: dict[str, str]  # symbol -> dataset label
    research_grade: bool  # every input is real, validated exchange data
    result_labels: tuple[str, ...] = RESULT_LABELS
    notes: list[str] = field(default_factory=list)


def result_checksum(res: RunResult, perf: PerformanceReport) -> str:
    """SHA-256 over every simulated artefact: orders+events, fills, ledger, equity, risk, metrics."""
    payload = {
        "status": res.status,
        "risk_verdict": res.risk_verdict,
        "orders": [
            [
                o.order_id,
                o.symbol,
                o.side,
                o.type,
                o.quantity,
                o.limit_price,
                o.stop_price,
                o.status,
                o.filled_qty,
                o.tag,
                o.client_id,
                o.reject_reason,
                o.created_ts,
                [[e.seq, e.ts, e.status, e.reason] for e in o.events],
            ]
            for o in res.orders
        ],
        "fills": [
            [
                f.fill_id,
                f.order_id,
                f.symbol,
                f.side,
                f.ts,
                f.price,
                f.quantity,
                f.liquidity,
                f.fee_asset,
                f.fee_amount,
                f.notional,
                f.reference_price,
                f.slippage_cost,
                f.realized_pnl,
            ]
            for f in res.fills
        ],
        "ledger": [
            [e.seq, e.ts, e.kind, e.asset, e.amount, e.order_id, e.fill_id]
            for e in res.ledger.entries
        ],
        "equity": [
            [p.ts, p.equity, p.cash, p.positions_value, p.reserved_cash, p.exposure]
            for p in res.equity
        ],
        "risk_events": [[e.seq, e.ts, e.kind, e.detail, e.equity] for e in res.risk_events],
        "checkpoints": [[c.index, c.ts, c.digest] for c in res.checkpoints],
        "performance": perf.to_json(),
    }
    return hashlib.sha256(canonical(payload).encode()).hexdigest()


def select_bars(
    bars: tuple[Bar, ...], tf: Timeframe, start: datetime | None, end: datetime | None, warmup: int
) -> list[Bar]:
    lo = None if start is None else start - timedelta(seconds=tf.seconds * warmup)
    return [
        b for b in bars if (lo is None or b.open_time >= lo) and (end is None or b.open_time < end)
    ]


def run_backtest(
    config: BacktestConfig,
    datasets: Mapping[str, Dataset],
    git_commit: str = "",
    should_cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    allow_degraded: bool = False,
) -> BacktestRun:
    """`allow_degraded` exists ONLY for deliberate stress tests on damaged data; such a run is never
    research-grade and is labelled as a stress test."""
    labels: dict[str, str] = {}
    research_grade = True
    for sym in config.symbols:
        ds = datasets.get(sym)
        if ds is None:
            raise RunRefused(f"dataset for {sym} not provided")
        if ds.meta.checksum != config.dataset_checksums[sym]:
            raise RunRefused(
                f"{sym}: dataset checksum does not match the configuration (wrong version)"
            )
        if ds.meta.timeframe != config.timeframe:
            raise RunRefused(
                f"{sym}: dataset timeframe {ds.meta.timeframe.value} != {config.timeframe.value}"
            )
        if ds.meta.symbol != sym:
            raise RunRefused(f"{sym}: dataset is for {ds.meta.symbol}")
        bad = ds.meta.validation_status is None or ds.meta.validation_status == Severity.REJECTED
        if bad and not allow_degraded:
            raise RunRefused(
                f"{sym}: dataset is REJECTED/unvalidated and cannot be used for results"
            )
        if bad:
            research_grade = False
        labels[sym] = ds.meta.label
        if not ds.meta.accepted_for_research:
            research_grade = False
    series: dict[str, Sequence[Bar]] = {
        s: select_bars(
            datasets[s].bars,
            config.timeframe,
            config.period_start,
            config.period_end,
            config.warmup_bars,
        )
        for s in config.symbols
    }
    if any(not v for v in series.values()):
        raise RunRefused("no bars in the requested period")
    rules = {r.symbol: r for r in config.rules}
    missing = [s for s in config.symbols if s not in rules]
    if missing:
        raise RunRefused(f"rule profile missing for {missing}")
    eng_cfg = EngineConfig(
        symbols=config.symbols,
        timeframe=config.timeframe,
        starting_cash=config.starting_cash,
        quote_asset=config.quote_asset,
        rules={s: rules[s] for s in config.symbols},
        fees=config.fees,
        execution=config.execution,
        risk=config.risk,
        latency_bars=config.latency_bars,
        trade_start=config.period_start if config.warmup_bars else None,
        checkpoint_every=config.checkpoint_every,
    )
    strategy = build_strategy(config.strategy_id, config.strategy_params)
    result = Engine(eng_cfg, series, strategy, should_cancel, on_progress).run()
    perf = compute_performance(result, config.timeframe, rules, config.starting_cash)
    notes: list[str] = []
    if allow_degraded:
        notes.append("STRESS TEST on deliberately degraded data: not a research result")
    if not research_grade:
        notes.append(
            "NOT research-grade: input includes fixture/synthetic, degraded or unvalidated data"
        )
    if not result.reconcile.ok:
        notes.append("LEDGER DID NOT RECONCILE: " + "; ".join(result.reconcile.issues))
    if any(not rules[s].verified for s in config.symbols) or not config.fees.verified:
        notes.append("exchange rules/fees are UNVERIFIED assumptions (see profile sources)")
    return BacktestRun(
        config=config,
        config_hash=config.config_hash(),
        result=result,
        performance=perf,
        result_checksum=result_checksum(result, perf),
        data_labels=labels,
        research_grade=research_grade,
        notes=notes,
    )


def is_non_research(source: SourceType) -> bool:
    return source in NON_RESEARCH_SOURCES
