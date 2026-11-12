"""Strategy A: regime-aware spot grid (long-only, cash- and inventory-backed).

Decisions use only closed bars. The grid never assumes every historical high/low produced a
profitable fill: buys are resting limit orders (conservative fill model), each filled lot gets a
resting sell one level up, and the grid is suspended in a downtrend, in high volatility, in cool-down
and when the risk engine has halted new buys. Below `lower * (1 - stop_buffer_pct)` it goes risk-off:
cancel everything and exit at market. Nothing here assumes a stop fills at its price.
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
from asta.research.strategies.indicators import ATR, EMA, SMA
from asta.research.types import OrderType, Side

BPS = Decimal(10_000)


@dataclass(frozen=True)
class GridParams:
    symbols: tuple[str, ...] = ("BTC-USDT", "ETH-USDT", "SOL-USDT")
    range_mode: str = "trailing"  # "trailing" (re-centred when flat) | "fixed"
    fixed_ranges: tuple[tuple[str, Decimal, Decimal], ...] = ()  # (symbol, lower, upper)
    range_lookback: int = 48  # bars of the SMA used as the grid centre (trailing mode)
    range_pct: Decimal = Decimal("0.08")  # total grid width as a fraction of the centre
    levels: int = 5  # number of buy levels (range is split into `levels` steps)
    level_notional: Decimal = Decimal(18)  # USDT per level
    max_exposure: Decimal = Decimal(
        90
    )  # per-symbol cap the strategy respects (risk engine is the hard cap)
    min_net_profit_bps: Decimal = Decimal(30)  # required net profit per round trip after fees
    cost_buffer_bps: Decimal = Decimal(10)  # extra margin for slippage/partial-fill uncertainty
    ema_fast: int = 24
    ema_slow: int = 96
    atr_period: int = 24
    max_atr_pct: Decimal = Decimal("0.03")  # suspend buys when ATR/close is above this
    stop_buffer_pct: Decimal = Decimal("0.01")  # risk-off when close < lower * (1 - buffer)
    cooldown_bars: int = 48
    recenter_bars: int = 24
    buy_ttl_bars: int = 72


@dataclass
class _Sym:
    ema_f: EMA
    ema_s: EMA
    atr: ATR
    sma: SMA
    levels: list[Decimal] = field(default_factory=list)
    lower: Decimal | None = None
    upper: Decimal | None = None
    lots: dict[int, Decimal] = field(default_factory=dict)  # level -> base qty awaiting its sell
    epoch: int = 0
    n: int = 0
    cooldown_until: int = -1
    built_at: int = -(10**9)
    disabled: str = ""


class RegimeGrid(Strategy):
    strategy_id: ClassVar[str] = "regime_grid"
    version: ClassVar[str] = "1.0.0"

    def __init__(self, params: GridParams) -> None:
        super().__init__(params)
        if params.levels < 1 or params.range_pct <= 0 or params.level_notional <= 0:
            raise ValueError("invalid grid parameters")
        self._fixed = {s: (lo, hi) for s, lo, hi in params.fixed_ranges}
        if params.range_mode == "fixed" and any(s not in self._fixed for s in params.symbols):
            raise ValueError("fixed range mode needs a range for every symbol")
        self._st = {
            s: _Sym(
                EMA(params.ema_fast),
                EMA(params.ema_slow),
                ATR(params.atr_period),
                SMA(params.range_lookback),
            )
            for s in params.symbols
        }
        self.notes: list[str] = []  # diagnostic reasons (e.g. grid disabled: spacing below costs)

    # ---------------------------------------------------------------- helpers
    def _build(self, ctx: StrategyContext, sym: str, st: _Sym, centre: Decimal, k: int) -> None:
        p = self.params
        if p.range_mode == "fixed":
            lower, upper = self._fixed[sym]
            centre = (lower + upper) / 2
        else:
            lower, upper = centre * (1 - p.range_pct / 2), centre * (1 + p.range_pct / 2)
        step = (upper - lower) / p.levels
        spacing_bps = step / centre * BPS
        required = 2 * ctx.fees.maker_bps + p.min_net_profit_bps + p.cost_buffer_bps
        st.built_at = k
        st.epoch += 1
        if spacing_bps < required:
            st.disabled = f"{sym}: spacing {spacing_bps:.1f} bps < required {required:.1f} bps"
            st.levels = []
            self.notes.append(st.disabled)
            return
        st.disabled = ""
        st.lower, st.upper = lower, upper
        tick = ctx.rules[sym].tick_size
        st.levels = [round_up(lower + step * i, tick) for i in range(p.levels + 1)]

    def _received(self, ctx: StrategyContext, qty: Decimal, liquidity: str) -> Decimal:
        bps = ctx.fees.maker_bps if liquidity == "MAKER" else ctx.fees.taker_bps
        if ctx.fees.fee_asset.value == "RECEIVED_ASSET":
            return qty - round_up(qty * bps / BPS)
        return qty

    # ---------------------------------------------------------------- decision
    def on_bar(self, ctx: StrategyContext) -> Sequence[Action]:
        p = self.params
        out: list[Action] = []
        for sym in p.symbols:
            if not ctx.has_bar(sym):
                continue
            st = self._st[sym]
            bar = ctx.bars(sym)[-1]
            close = bar.close
            f, s, a, c = (
                st.ema_f.update(close),
                st.ema_s.update(close),
                st.atr.update(bar),
                st.sma.update(close),
            )
            if ctx.is_warmup or f is None or s is None or a is None or c is None:
                continue
            rules = ctx.rules[sym]
            k = ctx.index
            # --- bookkeeping from fills that happened on the bar that just closed
            for fill in ctx.fills:
                if fill.symbol != sym:
                    continue
                if fill.side == Side.BUY and fill.tag.startswith("b:"):
                    lvl = int(fill.tag.split(":")[1])
                    qty = floor_to_step(
                        self._received(ctx, fill.quantity, fill.liquidity), rules.step_size
                    )
                    if qty >= rules.min_qty and lvl + 1 < len(st.levels):
                        st.n += 1
                        st.lots[lvl] = st.lots.get(lvl, Decimal(0)) + qty
                        out.append(
                            OrderIntent(
                                sym,
                                Side.SELL,
                                OrderType.LIMIT,
                                quantity=qty,
                                limit_price=st.levels[lvl + 1],
                                client_id=f"{sym}:g{st.epoch}:s{lvl}:{st.n}",
                                expire_after_bars=10**6,
                                tag=f"s:{lvl}",
                            )
                        )
                elif fill.side == Side.SELL and fill.tag.startswith("s:"):
                    lvl = int(fill.tag.split(":")[1])
                    st.lots[lvl] = max(Decimal(0), st.lots.get(lvl, Decimal(0)) - fill.quantity)
            pos = sellable_qty(ctx, sym)
            open_orders = ctx.open_orders(sym)
            # --- risk-off: below the range floor with inventory -> cancel all, exit at market
            if (
                pos >= rules.min_qty
                and st.lower is not None
                and close < st.lower * (1 - p.stop_buffer_pct)
            ):
                for o in open_orders:
                    if o.client_id:
                        out.append(CancelIntent(o.client_id))
                st.n += 1
                out.append(
                    OrderIntent(
                        sym,
                        Side.SELL,
                        OrderType.MARKET,
                        quantity=pos,
                        client_id=f"{sym}:g{st.epoch}:stop:{st.n}",
                        expire_after_bars=6,
                        tag="stop",
                    )
                )
                st.lots.clear()
                st.levels = []
                st.cooldown_until = k + p.cooldown_bars
                continue
            # --- regime suspension: no new buys; resting buys are cancelled
            downtrend = f < s
            hivol = close > 0 and a / close > p.max_atr_pct
            suspended = (
                downtrend
                or hivol
                or k < st.cooldown_until
                or ctx.risk.halted_new_buys
                or bool(st.disabled)
            )
            if suspended:
                for o in open_orders:
                    if o.side == Side.BUY and o.client_id:
                        out.append(CancelIntent(o.client_id))
                # a disabled/suspended grid may still re-centre once flat, so fall through only to that
                flat = pos < rules.min_qty and not [o for o in open_orders if o.side == Side.SELL]
                if not (
                    flat
                    and not (
                        downtrend or hivol or k < st.cooldown_until or ctx.risk.halted_new_buys
                    )
                ):
                    continue
            # --- (re)build the grid when absent, or when flat for long enough (trailing mode)
            flat = pos < rules.min_qty and not open_orders
            if not st.levels and not st.disabled:
                self._build(ctx, sym, st, c, k)
            elif p.range_mode == "trailing" and flat and k - st.built_at >= p.recenter_bars:
                self._build(ctx, sym, st, c, k)
            elif st.disabled and flat and k - st.built_at >= p.recenter_bars:
                self._build(ctx, sym, st, c, k)  # try again with the new centre/vol
            if not st.levels or st.upper is None or close > st.upper:
                continue  # end-of-range: no new buys above the grid
            open_buy_levels = {
                int(o.tag.split(":")[1])
                for o in open_orders
                if o.side == Side.BUY and o.tag.startswith("b:")
            }
            exposure = ctx.exposure(sym)
            for i in range(p.levels):
                price = st.levels[i]
                if (
                    price >= close * Decimal("0.9995")
                    or i in open_buy_levels
                    or st.lots.get(i, Decimal(0)) > 0
                ):
                    continue
                if exposure + p.level_notional > p.max_exposure:
                    break
                st.n += 1
                out.append(
                    OrderIntent(
                        sym,
                        Side.BUY,
                        OrderType.LIMIT,
                        quote_notional=p.level_notional,
                        limit_price=price,
                        client_id=f"{sym}:g{st.epoch}:b{i}:{st.n}",
                        expire_after_bars=p.buy_ttl_bars,
                        tag=f"b:{i}",
                    )
                )
                exposure += p.level_notional
        return out
