"""Offline simulation engine: clock, order lifecycle, fills, ledger, risk. Pure and deterministic.

Per timeline step (one bar close): (1) expire/gap orders and fill against the bar that just
closed (orders were decided on EARLIER bars), (2) mark equity, (3) update risk breakers, (4) give
the strategy the closed bar and collect intents, which activate no earlier than the next bar open.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from asta.core.money import QUANTUM, floor_to_step, round_down, round_up
from asta.core.order_gate import Surface, authorize
from asta.research.data.models import Bar
from asta.research.data.profiles import FeeAssetPolicy, FeeSchedule, RuleProfile
from asta.research.sim.context import (
    BarHistory,
    FillView,
    OrderView,
    PositionView,
    RiskView,
    StrategyContext,
)
from asta.research.sim.execution import ExecutionModel, quote_fill, tick_down, tick_up
from asta.research.sim.ledger import Ledger, LedgerError, ReconcileReport
from asta.research.sim.orders import CancelIntent, OrderEvent, OrderIntent, SimFill, SimOrder
from asta.research.sim.repeat_guard import RepeatRejectionGuard
from asta.research.sim.risk import RiskEngine, RiskEvent, SimRiskLimits
from asta.research.strategies.base import Strategy
from asta.research.types import OrderStatus, OrderType, Side, Timeframe

ZERO = Decimal(0)
BPS = Decimal(10_000)


class RunCancelled(Exception):
    pass


class EngineError(Exception):
    pass


@dataclass(frozen=True)
class EngineConfig:
    symbols: tuple[str, ...]
    timeframe: Timeframe
    starting_cash: Decimal
    quote_asset: str
    rules: dict[str, RuleProfile]
    fees: FeeSchedule
    execution: ExecutionModel
    risk: SimRiskLimits
    latency_bars: int = 0
    trade_start: datetime | None = None
    checkpoint_every: int = 500


@dataclass(frozen=True)
class EquityPoint:
    ts: datetime  # bar CLOSE time
    equity: Decimal
    cash: Decimal
    positions_value: Decimal
    reserved_cash: Decimal
    exposure: Decimal  # marked inventory + open buy-order notional (risk definition)


@dataclass(frozen=True)
class Checkpoint:
    index: int
    ts: datetime
    digest: str


@dataclass
class RunResult:
    status: str  # COMPLETED | STOPPED_RISK_HALT
    risk_verdict: str  # PASSED | BREACHED
    orders: list[SimOrder]
    fills: list[SimFill]
    ledger: Ledger
    equity: list[EquityPoint]
    risk_events: list[RiskEvent]
    checkpoints: list[Checkpoint]
    counters: dict[str, int]
    rejections: dict[str, int]
    final_marks: dict[str, Decimal]
    liquidation_equity: Decimal
    reconcile: ReconcileReport
    bars_processed: int
    warmup_bars: int
    trade_start_index: int | None
    stale_mark_steps: int = 0
    notes: list[str] = field(default_factory=list)


#: opt-in repeat guard re-evaluates a refused intent at least every N bars (5m: 1 h, 1h: 12 h)
REPEAT_REARM_BARS = 12


class PortfolioHooks(Protocol):
    """Optional portfolio-level coordination (M2 paper trading). When absent (every M1 run), the
    engine behaves exactly as before: all limits are judged on this account alone."""

    def extra_asset_exposure(self, base: str) -> Decimal: ...
    def extra_total_exposure(self) -> Decimal: ...
    def entry_block_reason(self, symbol: str) -> str | None: ...


class Engine:
    def __init__(
        self,
        cfg: EngineConfig,
        series: dict[str, Sequence[Bar]],
        strategy: Strategy,
        should_cancel: Callable[[], bool] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        risk: RiskEngine | None = None,
        hooks: PortfolioHooks | None = None,
        suppress_repeated_rejections: bool = False,
    ) -> None:
        self.cfg = cfg
        self.hooks = hooks
        #: opt-in (default OFF = identical to the accepted M1/M2 behaviour), see repeat_guard.py
        self.repeat_guard: RepeatRejectionGuard | None = (
            RepeatRejectionGuard() if suppress_repeated_rejections else None
        )
        self.strategy = strategy
        self.series = series
        self.should_cancel = should_cancel
        self.on_progress = on_progress
        self.tf = timedelta(seconds=cfg.timeframe.seconds)
        if self.repeat_guard is not None:
            self.repeat_guard.rearm_after = self.tf * REPEAT_REARM_BARS
        for s in cfg.symbols:
            base, quote = s.split("-")
            if quote != cfg.quote_asset:
                raise EngineError(f"{s}: quote {quote} != portfolio quote {cfg.quote_asset}")
            if s not in cfg.rules or s not in series:
                raise EngineError(f"{s}: rules and bars are required")
            prev: datetime | None = None
            for b in series[s]:
                if prev is not None and b.open_time <= prev:
                    raise EngineError(
                        f"{s}: bars must be strictly increasing (validate the dataset first)"
                    )
                prev = b.open_time
        self.timeline: list[datetime] = sorted(
            {b.open_time for s in cfg.symbols for b in series[s]}
        )
        self.ptr: dict[str, int] = {s: 0 for s in cfg.symbols}
        self.hist: dict[str, list[Bar]] = {s: [] for s in cfg.symbols}
        self.histories = {s: BarHistory(self.hist[s]) for s in cfg.symbols}
        self.marks: dict[str, Decimal] = {}  # keyed by symbol
        self.base_marks: dict[str, Decimal] = {}  # keyed by base asset (ledger valuation)
        self.ledger: Ledger | None = None
        self.risk = risk if risk is not None else RiskEngine(cfg.risk)
        self.orders: list[SimOrder] = []
        self.open: dict[str, list[SimOrder]] = {s: [] for s in cfg.symbols}
        self.fills: list[SimFill] = []
        self.equity_curve: list[EquityPoint] = []
        self.checkpoints: list[Checkpoint] = []
        self.clients: dict[str, int] = {}
        self.pools: dict[str, Decimal] = {}
        self.pool_members: dict[str, list[SimOrder]] = {}
        self.rejections: dict[str, int] = {}
        self.counters: dict[str, int] = {
            "warmup_dropped": 0,
            "gap_expiries": 0,
            "duplicate_ignored": 0,
            "orders_total": 0,
        }
        if self.repeat_guard is not None:
            self.counters["suppressed_repeat"] = 0
        self.fills_this_step: list[FillView] = []
        self._seq = 0
        self._order_id = 0
        self._fill_id = 0
        self.last_bar_ts: dict[str, datetime] = {}
        self.stale_mark_steps = 0
        self.warm_bars = 0
        self.trade_idx: int | None = None
        self.last_ts: datetime | None = None
        self._warm = False
        self._present: frozenset[str] = frozenset()

    @classmethod
    def live(
        cls,
        cfg: EngineConfig,
        strategy: Strategy,
        risk: RiskEngine | None = None,
        hooks: PortfolioHooks | None = None,
        suppress_repeated_rejections: bool = False,
    ) -> Engine:
        """Incrementally driven engine (paper trading): bars arrive through begin()/step()."""
        return cls(
            cfg,
            {s: [] for s in cfg.symbols},
            strategy,
            risk=risk,
            hooks=hooks,
            suppress_repeated_rejections=suppress_repeated_rejections,
        )

    # ------------------------------------------------------------------ helpers
    def _base(self, symbol: str) -> str:
        return symbol.split("-")[0]

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _led(self) -> Ledger:
        assert self.ledger is not None
        return self.ledger

    def _position_value(self, symbol: str) -> Decimal:
        pos = self._led().position(self._base(symbol))
        return pos.qty * self.marks[symbol] if pos.qty and symbol in self.marks else ZERO

    def _exposure(self, symbol: str) -> Decimal:
        open_buys = sum(
            (o.remaining * o.ref_price for o in self.open[symbol] if o.side == Side.BUY),
            ZERO,
        )
        return self._position_value(symbol) + open_buys

    def _total_exposure(self) -> Decimal:
        return sum((self._exposure(s) for s in self.cfg.symbols), ZERO)

    def _reject(self, ts: datetime, reason: str) -> None:
        self.rejections[reason] = self.rejections.get(reason, 0) + 1
        eq = self._led().equity(self.base_marks) if self.ledger else ZERO
        self.risk.record_rejection(ts, reason, eq)

    # ------------------------------------------------------------------ order intake
    def _guard_fingerprint(self, symbol: str) -> tuple[Decimal, ...]:
        """Discrete state only (no mark-dependent values): changes on fills, orders opening/closing, cash
        or held-quantity moves, or the same events in other accounts (hooks.state_token)."""
        led = self._led()
        token = getattr(self.hooks, "state_token", None) if self.hooks is not None else None
        return (
            Decimal(len(self.fills)),
            Decimal(sum(len(v) for v in self.open.values())),
            led.available(self.cfg.quote_asset),
            led.balance(self._base(symbol)),
            token() if token is not None else ZERO,
        )

    def _submit(self, intent: OrderIntent, decision_close: datetime, is_warmup: bool) -> None:
        if not authorize(Surface.SIMULATION).allowed:  # the single order-authorization contract
            self.counters["gate_denied"] = self.counters.get("gate_denied", 0) + 1  # never in M3
            return
        led = self._led()
        if intent.client_id is not None and intent.client_id in self.clients:
            self.counters["duplicate_ignored"] += 1  # idempotent: same key, no second order
            return
        guard_key: tuple[str, str, str, str] | None = None
        if self.repeat_guard is not None and intent.side == Side.BUY and not is_warmup:
            guard_key = (intent.symbol, intent.side.value, intent.type.value, intent.tag)
            if self.repeat_guard.should_suppress(
                guard_key, self._guard_fingerprint(intent.symbol), decision_close
            ):
                self.counters["suppressed_repeat"] += 1
                return  # a refusal that cannot have changed: no order, no row, no risk event
        self._order_id += 1
        self.counters["orders_total"] += 1
        sym = intent.symbol
        order = SimOrder(
            order_id=self._order_id,
            symbol=sym,
            side=intent.side,
            type=intent.type,
            quantity=ZERO,
            limit_price=intent.limit_price,
            stop_price=intent.stop_price,
            created_ts=decision_close,
            active_from=decision_close + self.tf * self.cfg.latency_bars,
            expire_ts=decision_close
            + self.tf * (self.cfg.latency_bars + max(1, intent.expire_after_bars)),
            tag=intent.tag,
            client_id=intent.client_id,
            oco_group=intent.oco_group,
        )
        self.orders.append(order)
        if intent.client_id is not None:
            self.clients[intent.client_id] = order.order_id

        def reject(reason: str) -> None:
            order.transition(OrderStatus.REJECTED, self._next_seq(), decision_close, reason)
            self._reject(decision_close, reason)
            if guard_key is not None and self.repeat_guard is not None:
                self.repeat_guard.record_rejection(
                    guard_key, reason, self._guard_fingerprint(intent.symbol), decision_close
                )

        order.events.append(
            OrderEvent(self._next_seq(), decision_close, order.order_id, OrderStatus.CREATED, "")
        )
        if is_warmup:
            self.counters["warmup_dropped"] += 1
            return reject("WARMUP")
        if sym not in self.cfg.rules:
            return reject("UNKNOWN_SYMBOL")
        rules = self.cfg.rules[sym]
        step, tick = rules.step_size, rules.tick_size
        # type/price sanity
        if intent.type == OrderType.LIMIT and (
            intent.limit_price is None or intent.limit_price <= 0
        ):
            return reject("LIMIT_PRICE_REQUIRED")
        if intent.type == OrderType.STOP_MARKET:
            if intent.side != Side.SELL:
                return reject("STOP_BUY_UNSUPPORTED")
            if intent.stop_price is None or intent.stop_price <= 0:
                return reject("STOP_PRICE_REQUIRED")
        if (intent.quantity is None) == (intent.quote_notional is None):
            return reject("SIZING_AMBIGUOUS")
        # prices on the tick grid, rounded against us
        if order.limit_price is not None:
            order.limit_price = (
                tick_down(order.limit_price, tick)
                if intent.side == Side.BUY
                else tick_up(order.limit_price, tick)
            )
        if order.stop_price is not None:
            order.stop_price = tick_up(order.stop_price, tick)
        ref = order.limit_price or order.stop_price or self.marks.get(sym)
        if ref is None or ref <= 0:
            return reject("NO_REFERENCE_PRICE")
        order.ref_price = ref
        raw_qty = (
            intent.quantity
            if intent.quantity is not None
            else (intent.quote_notional or ZERO) / ref
        )
        qty = floor_to_step(raw_qty, step)
        order.quantity = qty
        if qty <= 0 or qty < rules.min_qty:
            return reject("MIN_QTY")
        if qty * ref < rules.min_notional:
            return reject("MIN_NOTIONAL")
        base = self._base(sym)
        if intent.side == Side.BUY:
            extra_a = extra_t = ZERO
            if self.hooks is not None:
                blocked = self.hooks.entry_block_reason(sym)
                if blocked:
                    return reject(blocked)
                extra_a = self.hooks.extra_asset_exposure(base)
                extra_t = self.hooks.extra_total_exposure()
            after_asset = self._exposure(sym) + qty * ref + extra_a
            after_total = self._total_exposure() + qty * ref + extra_t
            why = self.risk.check_buy(after_asset, after_total)
            if why:
                return reject(why)
            notional_max = round_up(qty * ref * (1 + self.cfg.execution.reserve_buffer_bps / BPS))
            fee_q = ZERO
            if self.cfg.fees.fee_asset == FeeAssetPolicy.QUOTE:
                fee_q = round_up(notional_max * self.cfg.fees.taker_bps / BPS)
            need = notional_max + fee_q
            if need > led.available(self.cfg.quote_asset):
                return reject("INSUFFICIENT_CASH")
            led.reserve(decision_close, self.cfg.quote_asset, need, order.order_id)
            order.reserved_remaining = need
        else:
            pool = intent.oco_group or f"o{order.order_id}"
            order.pool = pool
            if pool in self.pools:
                if qty > self.pools[pool]:
                    return reject("INSUFFICIENT_INVENTORY")
            else:
                if qty > led.available(base):
                    return reject("INSUFFICIENT_INVENTORY")  # also the no-short-sale guarantee
                led.reserve(decision_close, base, qty, order.order_id)
                self.pools[pool] = qty
                self.pool_members[pool] = []
            self.pool_members[pool].append(order)
        order.transition(OrderStatus.VALIDATED, self._next_seq(), decision_close)
        order.transition(OrderStatus.SIMULATED_OPEN, self._next_seq(), decision_close)
        self.open[sym].append(order)
        if guard_key is not None and self.repeat_guard is not None:
            self.repeat_guard.record_accepted(guard_key)

    def _cancel(self, client_id: str, ts: datetime, reason: str = "STRATEGY_CANCEL") -> None:
        oid = self.clients.get(client_id)
        if oid is None:
            return
        o = self.orders[oid - 1]
        if not o.is_terminal:
            self._finish(o, OrderStatus.CANCELLED, ts, reason)

    # ------------------------------------------------------------------ order completion
    def _finish(self, o: SimOrder, status: OrderStatus, ts: datetime, reason: str = "") -> None:
        led = self._led()
        o.transition(status, self._next_seq(), ts, reason)
        if o in self.open[o.symbol]:
            self.open[o.symbol].remove(o)
        if o.side == Side.BUY:
            if o.reserved_remaining > 0:
                led.release(ts, self.cfg.quote_asset, o.reserved_remaining, o.order_id)
                o.reserved_remaining = ZERO
        else:
            members = self.pool_members.get(o.pool, [])
            if all(m.is_terminal for m in members) and o.pool in self.pools:
                left = self.pools.pop(o.pool)
                if left > 0:
                    led.release(ts, self._base(o.symbol), left, o.order_id)

    def _cancel_open_buys(self, ts: datetime, reason: str) -> None:
        for sym in self.cfg.symbols:
            for o in list(self.open[sym]):
                if o.side == Side.BUY:
                    self._finish(o, OrderStatus.CANCELLED, ts, reason)

    # ------------------------------------------------------------------ fills
    def _fit_buy_qty(self, o: SimOrder, price: Decimal, qty: Decimal, step: Decimal) -> Decimal:
        rate = (
            self.cfg.fees.taker_bps / BPS
            if self.cfg.fees.fee_asset == FeeAssetPolicy.QUOTE
            else ZERO
        )
        while qty > 0:
            cost = round_up(price * qty)
            cost += round_up(cost * rate) if rate else ZERO
            if cost <= o.reserved_remaining:
                return qty
            qty -= step
        return ZERO

    def _try_fill(self, o: SimOrder, bar: Bar) -> None:
        led = self._led()
        rules = self.cfg.rules[o.symbol]
        remaining = o.remaining
        if o.side == Side.SELL:
            remaining = min(remaining, self.pools.get(o.pool, ZERO))
        fq = quote_fill(o, bar, self.cfg.execution, rules, remaining)
        if fq is None:
            return
        price, qty = fq.price, fq.quantity
        bps = self.cfg.fees.maker_bps if fq.liquidity == "MAKER" else self.cfg.fees.taker_bps
        base = self._base(o.symbol)
        self._fill_id += 1
        try:
            if o.side == Side.BUY:
                qty = self._fit_buy_qty(o, price, qty, rules.step_size)
                if qty <= 0:
                    self._fill_id -= 1
                    return
                res = led.apply_buy(
                    bar.open_time,
                    base,
                    qty,
                    price,
                    bps,
                    self.cfg.fees.fee_asset,
                    o.order_id,
                    self._fill_id,
                )
                led.release(
                    bar.open_time, self.cfg.quote_asset, res.cash_out, o.order_id, self._fill_id
                )
                o.reserved_remaining -= res.cash_out
                notional, fee_amount, fee_asset, pnl = (
                    res.notional,
                    res.fee_amount,
                    res.fee_asset,
                    ZERO,
                )
            else:
                sres = led.apply_sell(
                    bar.open_time, base, qty, price, bps, o.order_id, self._fill_id
                )
                led.release(bar.open_time, base, qty, o.order_id, self._fill_id)
                self.pools[o.pool] -= qty
                notional, fee_amount, fee_asset, pnl = (
                    sres.notional,
                    sres.fee_amount,
                    self.cfg.quote_asset,
                    sres.realized_pnl,
                )
        except LedgerError as exc:  # invariant violation must never be hidden
            raise EngineError(f"ledger invariant failed on fill: {exc}") from exc
        slippage = ZERO if fq.liquidity == "MAKER" else abs(price - fq.reference) * qty
        fill = SimFill(
            self._fill_id,
            o.order_id,
            o.symbol,
            o.side,
            bar.open_time,
            price,
            qty,
            fq.liquidity,
            fee_asset,
            fee_amount,
            notional,
            fq.reference,
            round_up(slippage),
            pnl,
            o.tag,
        )
        self.fills.append(fill)
        self.fills_this_step.append(
            FillView(o.order_id, o.client_id, o.symbol, o.side, price, qty, fq.liquidity, o.tag)
        )
        o.filled_qty += qty
        if o.remaining <= 0:
            self._finish(o, OrderStatus.FILLED, bar.open_time)
        else:
            o.transition(OrderStatus.PARTIALLY_FILLED, self._next_seq(), bar.open_time)
        if o.side == Side.SELL and self.pools.get(o.pool, ZERO) <= 0 and o.pool in self.pools:
            for m in list(self.pool_members.get(o.pool, [])):
                if not m.is_terminal:
                    self._finish(m, OrderStatus.CANCELLED, bar.open_time, "OCO_POOL_EXHAUSTED")
            self.pools.pop(o.pool, None)

    def _process_bar(self, sym: str, bar: Bar) -> None:
        tfs = self.tf
        for o in list(self.open[sym]):
            if bar.open_time < o.active_from:
                continue
            gap = (
                bar.open_time != o.active_from
                if o.last_seen_bar is None
                else bar.open_time != o.last_seen_bar + tfs
            )
            if gap:
                self.counters["gap_expiries"] += 1
                self._finish(o, OrderStatus.EXPIRED, bar.open_time, "GAP")
            elif bar.open_time >= o.expire_ts:
                self._finish(o, OrderStatus.EXPIRED, bar.open_time, "EXPIRED")
        active = [o for o in self.open[sym] if bar.open_time >= o.active_from]
        stops = [o for o in active if o.type == OrderType.STOP_MARKET]
        markets = [o for o in active if o.type == OrderType.MARKET]
        buy_l = sorted(
            (o for o in active if o.type == OrderType.LIMIT and o.side == Side.BUY),
            key=lambda x: -(x.limit_price or ZERO),
        )
        sell_l = sorted(
            (o for o in active if o.type == OrderType.LIMIT and o.side == Side.SELL),
            key=lambda x: x.limit_price or ZERO,
        )
        for o in stops + markets + buy_l + sell_l:  # adverse-first: stops win ties
            if o.is_terminal:
                continue
            self._try_fill(o, bar)
            if not o.is_terminal:
                o.last_seen_bar = bar.open_time

    # ------------------------------------------------------------------ strategy context
    def _ctx(
        self, ts: datetime, k: int, present: frozenset[str], warm: bool, eq: Decimal
    ) -> StrategyContext:
        led = self._led()

        def position(sym: str) -> PositionView:
            p = led.position(self._base(sym))
            mark = self.marks.get(sym)
            return PositionView(
                sym, p.qty, p.cost_total, p.avg_cost, mark, p.qty * mark if mark else ZERO
            )

        def orders(sym: str) -> tuple[OrderView, ...]:
            return tuple(
                OrderView(
                    o.order_id,
                    o.client_id,
                    o.symbol,
                    o.side,
                    o.type,
                    o.quantity,
                    o.remaining,
                    o.limit_price,
                    o.stop_price,
                    o.status,
                    o.tag,
                )
                for o in self.open[sym]
            )

        st = self.risk.state
        return StrategyContext(
            now=ts + self.tf,
            index=k,
            is_warmup=warm,
            timeframe=self.cfg.timeframe,
            symbols=self.cfg.symbols,
            histories=self.histories,
            present=present,
            rules=self.cfg.rules,
            fees=self.cfg.fees,
            equity=eq,
            cash_available=led.available(self.cfg.quote_asset),
            positions=position,
            open_orders=orders,
            exposure=self._exposure,
            total_exposure=self._total_exposure(),
            fills=tuple(self.fills_this_step),
            risk=RiskView(st.halted_new_buys, st.daily_tripped, st.drawdown_halted),
            starting_equity=self.cfg.risk.starting_equity,
        )

    def _digest(self, k: int, ts: datetime) -> str:
        led = self._led()
        payload = {
            "k": k,
            "ts": ts.isoformat(),
            "bal": {a: str(v) for a, v in sorted(led._bal.items())},
            "res": {a: str(v) for a, v in sorted(led._res.items())},
            "pos": {a: [str(p.qty), str(p.cost_total)] for a, p in sorted(led.positions.items())},
            "open": sorted(
                (o.order_id, str(o.remaining)) for s in self.cfg.symbols for o in self.open[s]
            ),
            "risk": [
                str(self.risk.state.peak_equity),
                self.risk.state.daily_tripped,
                self.risk.state.drawdown_halted,
            ],
            "entries": len(led.entries),
            "fills": len(self.fills),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    # ------------------------------------------------------------------ main loop
    def begin(self, t0: datetime) -> None:
        """Open the ledger at t0 and notify the strategy. Call exactly once before step()."""
        cfg = self.cfg
        self.ledger = Ledger.open(cfg.quote_asset, cfg.starting_cash, t0)
        self.strategy.on_start(
            self._ctx(t0 - self.tf, -1, frozenset(), True, self.ledger.initial_cash)
        )

    def settle(
        self, k: int, ts: datetime, bars: dict[str, Bar], warm: bool | None = None
    ) -> Decimal:
        """Phase 1: fill/expire against the bars that just CLOSED (open time ts), mark, record the
        equity point. Returns this account's marked equity."""
        cfg = self.cfg
        led = self._led()
        present: set[str] = set()
        self.fills_this_step = []
        self._warm = (
            (cfg.trade_start is not None and ts < cfg.trade_start) if warm is None else warm
        )
        if not self._warm and self.trade_idx is None:
            self.trade_idx = k
        if self._warm:
            self.warm_bars += 1
        for sym in cfg.symbols:
            bar = bars.get(sym)
            if bar is not None and bar.open_time == ts:
                present.add(sym)
                self._process_bar(sym, bar)
                self.hist[sym].append(bar)
                self.marks[sym] = bar.close
                self.base_marks[self._base(sym)] = bar.close
                self.last_bar_ts[sym] = ts
        self._present = frozenset(present)
        if len(present) < len(cfg.symbols):
            self.stale_mark_steps += 1
        eq = led.equity(self.base_marks)
        if not self._warm:
            pv = led.positions_value(self.base_marks)
            self.equity_curve.append(
                EquityPoint(
                    ts + self.tf,
                    eq,
                    led.balance(cfg.quote_asset),
                    pv,
                    led.reserved(cfg.quote_asset),
                    self._total_exposure(),
                )
            )
        return eq

    def after_risk(self, ts: datetime, tripped: bool) -> None:
        """A (new) breaker trip cancels this account's open buys; exits are never blocked."""
        if tripped:
            self._cancel_open_buys(ts + self.tf, "RISK_TRIP")

    def decide(self, k: int, ts: datetime, eq: Decimal, strategy_active: bool = True) -> None:
        """Phase 2: strategy decisions at bar close (orders activate from the next bar).
        ``strategy_active=False`` (paused / halted session): the strategy is STILL consulted with
        ``is_warmup=True`` so its internal indicator state stays in step with the market, but every
        order it proposes is rejected as WARMUP and cancels are ignored: the session cannot
        create or change any simulated order."""
        cfg = self.cfg
        close_ts = ts + self.tf
        mute = self._warm or not strategy_active
        ctx = self._ctx(ts, k, self._present, mute, eq)
        for act in self.strategy.on_bar(ctx):
            if isinstance(act, CancelIntent):
                if strategy_active:
                    self._cancel(act.client_id, close_ts)
            else:
                self._submit(act, close_ts, mute)
        if cfg.checkpoint_every and k % cfg.checkpoint_every == 0:
            self.checkpoints.append(Checkpoint(k, close_ts, self._digest(k, ts)))
        self.last_ts = ts

    def step(self, k: int, ts: datetime, bars: dict[str, Bar]) -> None:
        """M1 path: settle, update this run's own risk engine, decide."""
        eq = self.settle(k, ts, bars)
        if not self._warm and self.risk.on_equity(ts, eq):
            self.after_risk(ts, True)
        self.decide(k, ts, eq)

    def finish(self, n_steps: int) -> RunResult:
        """Cancel what is still open, reconcile and package the result."""
        cfg = self.cfg
        led = self._led()
        assert self.last_ts is not None
        end_ts = self.last_ts + self.tf
        for sym in cfg.symbols:
            for o in list(self.open[sym]):
                self._finish(o, OrderStatus.CANCELLED, end_ts, "END_OF_DATA")
        rec = led.reconcile(self.base_marks)
        ex = cfg.execution
        liq = led.balance(cfg.quote_asset)
        haircut = (cfg.fees.taker_bps + ex.base_slippage_bps + ex.half_spread_bps) / BPS
        for sym in cfg.symbols:
            pos = led.position(self._base(sym))
            if pos.qty and sym in self.marks:
                liq += round_down(pos.qty * self.marks[sym] * (1 - haircut))
        st = self.risk.state
        breached = st.drawdown_halted or any(e.kind == "DAILY_LOSS_TRIP" for e in st.events)
        return RunResult(
            status="STOPPED_RISK_HALT" if st.drawdown_halted else "COMPLETED",
            risk_verdict="BREACHED" if breached else "PASSED",
            orders=self.orders,
            fills=self.fills,
            ledger=led,
            equity=self.equity_curve,
            risk_events=st.events,
            checkpoints=self.checkpoints,
            counters=self.counters,
            rejections=self.rejections,
            final_marks=dict(self.marks),
            liquidation_equity=liq,
            reconcile=rec,
            bars_processed=n_steps,
            warmup_bars=self.warm_bars,
            trade_start_index=self.trade_idx,
            stale_mark_steps=self.stale_mark_steps,
        )

    def run(self) -> RunResult:
        n = len(self.timeline)
        if n == 0:
            raise EngineError("no bars to simulate")
        self.begin(self.timeline[0])
        for k, ts in enumerate(self.timeline):
            if self.should_cancel and k % 256 == 0 and self.should_cancel():
                raise RunCancelled()
            if self.on_progress and k % 256 == 0:
                self.on_progress(k, n)
            bars: dict[str, Bar] = {}
            for sym in self.cfg.symbols:
                i = self.ptr[sym]
                sb = self.series[sym]
                if i < len(sb) and sb[i].open_time == ts:
                    bars[sym] = sb[i]
                    self.ptr[sym] = i + 1
            self.step(k, ts, bars)
        return self.finish(n)


__all__ = [
    "QUANTUM",
    "Engine",
    "EngineConfig",
    "EngineError",
    "PortfolioHooks",
    "RunCancelled",
    "RunResult",
]
