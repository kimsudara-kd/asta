"""Paper-trading runtime (M2-B/C): pure, deterministic, no I/O.

Drives the SAME simulation engine as M1 (ledger, fill model, risk rules, strategies) one closed
bar-set at a time. Input per step: the exact closed bars of every enabled venue/symbol, a map of
symbols blocked from NEW entries (stale/missing data) and whether strategies may decide. Output:
the rows to persist. Nothing here can reach an exchange: there is no client, signer or credential.

Accounts: one engine per (venue, strategy) wallet slice + two shadow benchmark accounts.
Portfolio equity = sum of active accounts + virtual cash of venues without a verified feed.
Risk is ONE shared engine judged on global equity/exposure (accounts are not independent bots).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from asta.core.money import dec_str
from asta.paper.config import (
    BENCHMARK_ROLES,
    AccountSpec,
    PaperSessionConfig,
)
from asta.research.data.models import Bar
from asta.research.sim.engine import Engine, EngineConfig
from asta.research.sim.execution import ExecutionModel
from asta.research.sim.orders import SimOrder
from asta.research.sim.risk import RiskEngine, SimRiskLimits
from asta.research.strategies.registry import REGISTRY, build_strategy
from asta.research.types import OrderStatus

ZERO = Decimal(0)
OPEN_NAME = {OrderStatus.SIMULATED_OPEN: "OPEN"}


def order_state(status: OrderStatus) -> str:
    return OPEN_NAME.get(status, status.value)


@dataclass
class StepRows:
    """Everything one step produced (persisted in ONE transaction by the store)."""

    orders: list[dict[str, Any]] = field(default_factory=list)  # upserts (current state)
    order_events: list[dict[str, Any]] = field(default_factory=list)
    fills: list[dict[str, Any]] = field(default_factory=list)
    ledger: list[dict[str, Any]] = field(default_factory=list)
    signals: list[dict[str, Any]] = field(default_factory=list)
    balances: list[dict[str, Any]] = field(default_factory=list)
    risk_events: list[dict[str, Any]] = field(default_factory=list)
    equity: dict[str, Any] | None = None
    checkpoint: dict[str, Any] | None = None


class _Hooks:
    """PortfolioHooks for one account: other accounts' exposure + stale-data entry blocks."""

    def __init__(self, rt: PaperRuntime, key: str, venue: str) -> None:
        self.rt, self.key, self.venue = rt, key, venue

    def extra_asset_exposure(self, base: str) -> Decimal:
        return sum(
            (a.asset_exposure(base) for k, a in self.rt.active.items() if k != self.key), ZERO
        )

    def extra_total_exposure(self) -> Decimal:
        return sum((a.total_exposure() for k, a in self.rt.active.items() if k != self.key), ZERO)

    def entry_block_reason(self, symbol: str) -> str | None:
        return self.rt.blocked.get(f"{self.venue}:{symbol}")


class SubAccount:
    def __init__(self, rt: PaperRuntime, spec: AccountSpec, cfg: PaperSessionConfig) -> None:
        self.spec = spec
        self.cfg = cfg
        rules = {r.symbol: r for r in cfg.rules[spec.venue]}
        risk: RiskEngine
        self.hooks: _Hooks | None = None
        if spec.counts_toward_portfolio:
            self.hooks = _Hooks(rt, spec.key, spec.venue)
            risk = rt.risk
        elif spec.role == "BENCHMARK_UNCONSTRAINED":
            risk = RiskEngine(SimRiskLimits.unconstrained_reference(cfg.total_equity))
        else:
            risk = RiskEngine(SimRiskLimits.default(cfg.total_equity))
        ecfg = EngineConfig(
            symbols=cfg.symbols,
            timeframe=cfg.timeframe,
            starting_cash=spec.starting_cash,
            quote_asset=cfg.quote_asset,
            rules=rules,
            fees=cfg.fees[spec.venue],
            execution=cfg.execution,
            risk=risk.limits,
            latency_bars=cfg.latency_bars,
            trade_start=None,
            checkpoint_every=0,  # the runtime emits its own per-step checkpoint
        )
        self.strategy = build_strategy(spec.strategy_id, spec.params)
        self.engine = Engine.live(ecfg, self.strategy, risk=risk, hooks=self.hooks)
        self.began = False
        self.version = REGISTRY[spec.strategy_id].version
        # persistence cursors
        self._n_orders = 0
        self._order_sig: dict[int, tuple[str, Decimal, int]] = {}
        self._n_fills = 0
        self._n_ledger = 0
        self.last_equity: Decimal = spec.starting_cash
        self.settled_equity: Decimal = spec.starting_cash

    # ---- exposure views used by other accounts' hooks
    def asset_exposure(self, base: str) -> Decimal:
        if not self.began:
            return ZERO
        sym = f"{base}-{self.cfg.quote_asset}"
        return self.engine._exposure(sym) if sym in self.engine.cfg.symbols else ZERO

    def total_exposure(self) -> Decimal:
        return self.engine._total_exposure() if self.began else ZERO

    # ---- extraction
    def extract(
        self,
        rows: StepRows,
        step_ts: datetime,
        persist_money: bool,
        persist_orders: bool,
        persist_balances: bool,
    ) -> None:
        eng, led, venue = self.engine, self.engine.ledger, self.spec.venue
        assert led is not None
        key = self.spec.key
        if persist_orders:
            for o in eng.orders[self._n_orders :]:
                rows.signals.append(
                    {
                        "account": key,
                        "signal_id": o.order_id,
                        "step_ts": step_ts,
                        "strategy_id": self.spec.strategy_id,
                        "strategy_version": self.version,
                        "venue": venue,
                        "symbol": o.symbol,
                        "side": o.side.value,
                        "order_type": o.type.value,
                        "tag": o.tag,
                        "accepted": o.status != OrderStatus.REJECTED,
                        "reason": o.reject_reason,
                    }
                )
            for o in eng.orders:
                sig = (o.status.value, o.filled_qty, len(o.events))
                if self._order_sig.get(o.order_id) == sig:
                    continue
                rows.orders.append({"account": key, **self._order_row(o, venue)})
                known = self._order_sig.get(o.order_id)
                for ev in o.events[(known[2] if known else 0) :]:
                    rows.order_events.append(
                        {
                            "account": key,
                            "order_id": o.order_id,
                            "seq": ev.seq,
                            "ts": ev.ts,
                            "status": order_state(ev.status),
                            "reason": ev.reason,
                        }
                    )
        if persist_money:
            for f in eng.fills[self._n_fills :]:
                rows.fills.append(
                    {
                        "account": key,
                        "fill_id": f.fill_id,
                        "order_id": f.order_id,
                        "venue": venue,
                        "symbol": f.symbol,
                        "side": f.side.value,
                        "ts": f.ts,
                        "price": f.price,
                        "quantity": f.quantity,
                        "liquidity": f.liquidity,
                        "fee_asset": f.fee_asset,
                        "fee_amount": f.fee_amount,
                        "notional": f.notional,
                        "reference_price": f.reference_price,
                        "slippage_cost": f.slippage_cost,
                        "realized_pnl": f.realized_pnl,
                        "tag": f.tag,
                    }
                )
            for e in led.entries[self._n_ledger :]:
                rows.ledger.append(
                    {
                        "account": key,
                        "seq": e.seq,
                        "ts": e.ts,
                        "kind": e.kind,
                        "asset": e.asset,
                        "amount": e.amount,
                        "order_id": e.order_id,
                        "fill_id": e.fill_id,
                        "note": e.note,
                    }
                )
            assets = {self.cfg.quote_asset} | {s.split("-")[0] for s in eng.cfg.symbols}
            # balances (with their marks) are re-emitted on EVERY live step, also when strategies are
            # muted (paused/late): the equity snapshot is marked each step and reconciliation
            # compares it with balances x marks
            for a in sorted(assets if (persist_balances or rows.ledger or rows.fills) else ()):
                pos = led.positions.get(a)
                rows.balances.append(
                    {
                        "account": key,
                        "asset": a,
                        "balance": led.balance(a),
                        "reserved": led.reserved(a),
                        "cost_total": pos.cost_total if pos else ZERO,
                        "mark": eng.base_marks.get(a),
                    }
                )
        # cursors advance whether or not rows are emitted (warm-up steps emit nothing)
        for o in eng.orders:
            self._order_sig[o.order_id] = (o.status.value, o.filled_qty, len(o.events))
        self._n_orders = len(eng.orders)
        self._n_fills = len(eng.fills)
        self._n_ledger = len(led.entries)

    @staticmethod
    def _order_row(o: SimOrder, venue: str) -> dict[str, Any]:
        return {
            "order_id": o.order_id,
            "venue": venue,
            "symbol": o.symbol,
            "side": o.side.value,
            "order_type": o.type.value,
            "quantity": o.quantity,
            "filled_qty": o.filled_qty,
            "limit_price": o.limit_price,
            "status": order_state(o.status),
            "reason": o.reject_reason,
            "tag": o.tag,
            "created_ts": o.created_ts,
            "updated_ts": o.events[-1].ts if o.events else o.created_ts,
        }


class PaperRuntime:
    def __init__(self, cfg: PaperSessionConfig) -> None:
        cfg.validate()
        self.cfg = cfg
        self.risk = RiskEngine(cfg.risk_limits, auto_reset_daily=False)
        self.blocked: dict[str, str] = {}
        self.tf_seconds = cfg.timeframe.seconds
        specs = cfg.accounts()
        self.accounts: dict[str, SubAccount] = {}
        self.active: dict[str, SubAccount] = {}
        for sp in specs:
            sa = SubAccount(self, sp, cfg)
            self.accounts[sp.key] = sa
            if sp.counts_toward_portfolio:
                self.active[sp.key] = sa
        #: virtual cash of venues without a verified feed: stays uninvested, never moved.
        self.idle_cash = sum(
            (v for k, v in cfg.wallets.items() if k not in cfg.enabled_venues), ZERO
        )
        self._risk_cursor = 0
        self.step_seq = -1
        self.last_step_ts: datetime | None = None
        self.last_global_equity = cfg.total_equity

    # ------------------------------------------------------------------ queries
    def global_equity(self) -> Decimal:
        return self.idle_cash + sum((a.settled_equity for a in self.active.values()), ZERO)

    def halted(self) -> bool:
        return self.risk.state.halted_new_buys

    # ------------------------------------------------------------------ one step
    def step(
        self,
        step_seq: int,
        ts: datetime,
        bars: Mapping[str, Bar],
        blocked: Mapping[str, str],
        strategy_active: bool,
        warm: bool,
    ) -> StepRows:
        if step_seq != self.step_seq + 1:
            raise ValueError(
                f"steps must be consecutive: expected {self.step_seq + 1}, got {step_seq}"
            )
        self.blocked = dict(blocked)
        rows = StepRows()
        # phase 1: settle every account against the bars that just closed
        for acc in self.accounts.values():
            if not acc.began:
                acc.engine.begin(ts)
                acc.began = True
            own = {
                s: bars[f"{acc.spec.venue}:{s}"]
                for s in acc.engine.cfg.symbols
                if f"{acc.spec.venue}:{s}" in bars
            }
            acc.settled_equity = acc.engine.settle(step_seq, ts, own, warm)
        # phase 2: ONE global risk evaluation (portfolio equity across venues and strategies)
        geq = self.global_equity()
        self.last_global_equity = geq
        tripped = bool(self.risk.on_equity(ts, geq)) if not warm else False
        # phase 3: decisions (benchmarks run on their own risk engines)
        for acc in self.accounts.values():
            if acc.spec.role in BENCHMARK_ROLES:
                if not warm and acc.engine.risk.on_equity(ts, acc.settled_equity):
                    acc.engine.after_risk(ts, True)
                acc.engine.decide(step_seq, ts, acc.settled_equity, True)
                continue
            if tripped:
                acc.engine.after_risk(ts, True)
            acc.engine.decide(step_seq, ts, acc.settled_equity, strategy_active)
        for acc in self.accounts.values():
            counts = acc.spec.counts_toward_portfolio
            acc.extract(
                rows,
                ts,
                persist_money=counts,
                persist_orders=counts and not warm and strategy_active,
                persist_balances=counts and not warm,
            )
        # shared risk events (global)
        for e in self.risk.state.events[self._risk_cursor :]:
            rows.risk_events.append(
                {"seq": e.seq, "ts": e.ts, "kind": e.kind, "detail": e.detail, "equity": e.equity}
            )
        self._risk_cursor = len(self.risk.state.events)
        self.step_seq = step_seq
        self.last_step_ts = ts
        if not warm:
            rows.equity = self._equity_row(step_seq, ts)
        rows.checkpoint = self._checkpoint(step_seq, ts)
        return rows

    # ------------------------------------------------------------------ outputs
    def _equity_row(self, step_seq: int, ts: datetime) -> dict[str, Any]:
        tf = self.tf_seconds
        from datetime import timedelta

        close_ts = ts + timedelta(seconds=tf)
        accounts: dict[str, Any] = {}
        cash = self.idle_cash
        exposure = ZERO
        for k, a in self.active.items():
            led = a.engine.ledger
            assert led is not None
            c = led.balance(self.cfg.quote_asset)
            cash += c
            exposure += a.total_exposure()
            accounts[k] = {
                "equity": dec_str(a.settled_equity),
                "cash": dec_str(c),
                "exposure": dec_str(a.total_exposure()),
                "venue": a.spec.venue,
            }
        bench = {
            k: dec_str(a.settled_equity) for k, a in self.accounts.items() if k.startswith("BENCH:")
        }
        bench["BENCH:all_cash"] = dec_str(self.cfg.total_equity)
        return {
            "step_seq": step_seq,
            "ts": close_ts,
            "equity": self.global_equity(),
            "cash": cash,
            "exposure": exposure,
            "accounts": accounts,
            "benchmarks": bench,
        }

    def digest(self, step_seq: int, ts: datetime) -> str:
        parts = {k: a.engine._digest(step_seq, ts) for k, a in sorted(self.accounts.items())}
        st = self.risk.state
        payload = {
            "accounts": parts,
            "risk": [
                str(st.peak_equity),
                st.daily_tripped,
                st.drawdown_halted,
                len(st.events),
            ],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def _checkpoint(self, step_seq: int, ts: datetime) -> dict[str, Any]:
        return {
            "step_seq": step_seq,
            "digest": self.digest(step_seq, ts),
            "summary": {
                "global_equity": dec_str(self.global_equity()),
                "halted": self.halted(),
                "fills": {k: len(a.engine.fills) for k, a in self.accounts.items()},
                "ledger_entries": {
                    k: len(a.engine.ledger.entries) if a.engine.ledger else 0
                    for k, a in self.accounts.items()
                },
                "orders": {k: len(a.engine.orders) for k, a in self.accounts.items()},
            },
        }

    # ------------------------------------------------------------------ operator actions
    def rearm_daily(self, ts: datetime) -> str | None:
        """Explicit operator release of a latched daily-loss trip (later UTC day only)."""
        return self.risk.rearm_daily(ts, self.global_equity())


__all__ = ["ExecutionModel", "PaperRuntime", "StepRows", "SubAccount", "order_state"]
