"""Strategy B: trend pullback (long-only, fully causal indicators).

Enter on a pullback inside a measured uptrend; exit through a resting take-profit / protective stop
pair (OCO: the stop wins ties in the simulator), a time stop, or an adverse regime change. After an
exit a cool-down blocks re-entry. Entry requires an expected edge larger than round-trip costs.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import ClassVar

from asta.core.money import floor_to_step, round_up
from asta.research.sim.context import StrategyContext
from asta.research.sim.orders import CancelIntent, OrderIntent
from asta.research.strategies.base import Action, Strategy, sellable_qty
from asta.research.strategies.indicators import ATR, EMA
from asta.research.types import OrderType, Side

BPS = Decimal(10_000)


@dataclass(frozen=True)
class TrendPullbackParams:
    symbols: tuple[str, ...] = ("BTC-USDT", "ETH-USDT", "SOL-USDT")
    ema_fast: int = 24
    ema_slow: int = 96
    slope_bars: int = 12  # slow EMA must be higher than `slope_bars` bars ago
    atr_period: int = 24
    pullback_atr: Decimal = Decimal("0.5")  # EMA_fast - close must be >= this * ATR
    min_atr_pct: Decimal = Decimal("0.004")  # ATR/close floor: below it fees dominate
    max_atr_pct: Decimal = Decimal("0.04")  # ATR/close ceiling: above it the regime is too wild
    entry_notional: Decimal = Decimal(90)
    risk_per_trade_pct: Decimal = Decimal(0)  # 0 = fixed notional; else risk-based sizing
    tp_atr: Decimal = Decimal("3.0")
    sl_atr: Decimal = Decimal("1.5")
    min_net_edge_bps: Decimal = Decimal(20)  # required expected edge beyond round-trip costs
    max_hold_bars: int = 96
    cooldown_bars: int = 24  # no re-entry for this many bars after any exit
    exit_on_trend_break: bool = True


@dataclass
class _Sym:
    ema_f: EMA
    ema_s: EMA
    atr: ATR
    slow_hist: list[Decimal] = field(default_factory=list)
    entry_index: int | None = None
    entry_atr: Decimal = Decimal(0)
    oco_placed: bool = False
    cooldown_until: int = -1
    n: int = 0
    entry_cid: str | None = None


class TrendPullback(Strategy):
    strategy_id: ClassVar[str] = "trend_pullback"
    version: ClassVar[str] = "1.0.0"

    def __init__(self, params: TrendPullbackParams) -> None:
        super().__init__(params)
        if params.sl_atr <= 0 or params.tp_atr <= 0:
            raise ValueError("stop and target distances must be positive")
        self._st = {
            s: _Sym(EMA(params.ema_fast), EMA(params.ema_slow), ATR(params.atr_period))
            for s in params.symbols
        }

    def on_bar(self, ctx: StrategyContext) -> Sequence[Action]:
        p = self.params
        out: list[Action] = []
        for sym in p.symbols:
            if not ctx.has_bar(sym):
                continue
            st = self._st[sym]
            bar = ctx.bars(sym)[-1]
            close = bar.close
            f, s, a = st.ema_f.update(close), st.ema_s.update(close), st.atr.update(bar)
            if s is not None:
                st.slow_hist.append(s)
            if (
                ctx.is_warmup
                or f is None
                or s is None
                or a is None
                or len(st.slow_hist) <= p.slope_bars
            ):
                continue
            rules = ctx.rules[sym]
            k = ctx.index
            pos = sellable_qty(ctx, sym)
            orders = ctx.open_orders(sym)
            holding = pos >= rules.min_qty

            if holding:
                if st.entry_index is None:
                    st.entry_index = k
                # protective pair is placed once, sized to the ACTUAL position (partial fills ok)
                if not st.oco_placed:
                    for o in orders:  # cancel any unfilled entry remainder first
                        if o.side == Side.BUY and o.client_id:
                            out.append(CancelIntent(o.client_id))
                    avg = ctx.position(sym).avg_cost or close
                    qty = floor_to_step(pos, rules.step_size)
                    st.n += 1
                    grp = f"{sym}:oco{st.n}"
                    out.append(
                        OrderIntent(
                            sym,
                            Side.SELL,
                            OrderType.LIMIT,
                            quantity=qty,
                            limit_price=avg + p.tp_atr * st.entry_atr,
                            client_id=f"{grp}:tp",
                            expire_after_bars=10**6,
                            oco_group=grp,
                            tag="tp",
                        )
                    )
                    out.append(
                        OrderIntent(
                            sym,
                            Side.SELL,
                            OrderType.STOP_MARKET,
                            quantity=qty,
                            stop_price=avg - p.sl_atr * st.entry_atr,
                            client_id=f"{grp}:sl",
                            expire_after_bars=10**6,
                            oco_group=grp,
                            tag="sl",
                        )
                    )
                    st.oco_placed = True
                    continue
                held = k - (st.entry_index or k)
                trend_break = p.exit_on_trend_break and f < s
                if held >= p.max_hold_bars or trend_break:
                    for o in orders:
                        if o.client_id:
                            out.append(CancelIntent(o.client_id))
                    st.n += 1
                    out.append(
                        OrderIntent(
                            sym,
                            Side.SELL,
                            OrderType.MARKET,
                            quantity=floor_to_step(pos, rules.step_size),
                            client_id=f"{sym}:exit{st.n}",
                            expire_after_bars=6,
                            tag="time_exit" if held >= p.max_hold_bars else "trend_exit",
                        )
                    )
                    st.cooldown_until = k + p.cooldown_bars
                    st.entry_index = None
                    st.oco_placed = False
                continue

            # flat
            if st.entry_index is not None or st.oco_placed:  # position just closed (tp/sl/exit)
                st.entry_index = None
                st.oco_placed = False
                st.cooldown_until = max(st.cooldown_until, k + p.cooldown_bars)
            if orders:  # an entry is still working (or an exit is settling)
                continue
            if k < st.cooldown_until or ctx.risk.halted_new_buys:
                continue
            vol = a / close if close > 0 else Decimal(1)
            uptrend = f > s and s > st.slow_hist[-1 - p.slope_bars] and close > s
            pullback = (f - close) >= p.pullback_atr * a
            if not (uptrend and pullback and p.min_atr_pct <= vol <= p.max_atr_pct):
                continue
            costs_bps = 2 * ctx.fees.taker_bps
            expected_bps = p.tp_atr * a / close * BPS
            if expected_bps < costs_bps + p.min_net_edge_bps:
                continue  # the move we are targeting does not pay for the costs
            notional = p.entry_notional
            if p.risk_per_trade_pct > 0:
                risk_cash = ctx.starting_equity * p.risk_per_trade_pct
                dist = p.sl_atr * a
                notional = min(p.entry_notional, risk_cash / (dist / close))
            if notional < rules.min_notional or ctx.exposure(sym) + notional > ctx.starting_equity:
                continue
            st.n += 1
            st.entry_atr = a
            out.append(
                OrderIntent(
                    sym,
                    Side.BUY,
                    OrderType.MARKET,
                    quote_notional=round_up(notional, Decimal("0.01")),
                    client_id=f"{sym}:entry{st.n}",
                    expire_after_bars=3,
                    tag="entry",
                )
            )
        return out
