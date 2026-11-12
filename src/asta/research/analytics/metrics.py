"""Performance analytics computed ONLY from persisted simulation artefacts (equity points, fills,
ledger positions). Undefined or statistically invalid metrics are N/A (value None + reason), never
zero or a misleading number (ADR-0007). Daily statistics: UTC days, risk-free 0, sqrt(365)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from asta.core.money import quantize
from asta.research.data.profiles import RuleProfile
from asta.research.sim.engine import EquityPoint, RunResult
from asta.research.types import OrderStatus, Side, Timeframe

ZERO = Decimal(0)
ONE = Decimal(1)
MIN_DAYS_FOR_RATIOS = 30  # Sharpe/Sortino/volatility need at least this many daily observations
MIN_DAYS_FOR_ANNUALISING = 90
SQRT_365 = Decimal(365).sqrt()


def NA(reason: str) -> dict[str, Any]:  # noqa: N802 - sentinel constructor
    return {"value": None, "na_reason": reason}


def is_na(v: Any) -> bool:
    return isinstance(v, dict) and "na_reason" in v


@dataclass
class PerformanceReport:
    returns: dict[str, Any] = field(default_factory=dict)
    risk: dict[str, Any] = field(default_factory=dict)
    trading: dict[str, Any] = field(default_factory=dict)
    portfolio: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "returns": self.returns,
            "risk": self.risk,
            "trading": self.trading,
            "portfolio": self.portfolio,
            "meta": self.meta,
        }

    def value(self, section: str, key: str) -> Decimal | None:
        v = getattr(self, section)[key]
        if is_na(v) or v is None:
            return None
        return v if isinstance(v, Decimal) else Decimal(str(v))


def _q(x: Decimal) -> Decimal:
    return quantize(x, Decimal("1e-8"))


def _day(ts: datetime) -> Any:
    return (
        ts - timedelta(microseconds=1)
    ).date()  # a bar closing at 00:00 belongs to the prior day


def _std(xs: list[Decimal], sample: bool = True) -> Decimal:
    n = len(xs)
    mean = sum(xs, ZERO) / n
    var = sum(((x - mean) ** 2 for x in xs), ZERO) / (n - 1 if sample else n)
    return var.sqrt()


def daily_equity(points: list[EquityPoint]) -> list[tuple[Any, Decimal]]:
    last: dict[Any, Decimal] = {}
    for p in points:
        last[_day(p.ts)] = p.equity
    return sorted(last.items())


def daily_returns(points: list[EquityPoint], start_equity: Decimal) -> list[Decimal]:
    prev = start_equity
    out: list[Decimal] = []
    for _, e in daily_equity(points):
        out.append(e / prev - ONE)
        prev = e
    return out


def drawdown_stats(points: list[EquityPoint], start_equity: Decimal) -> dict[str, Any]:
    """Drawdown depth and duration. Duration runs from the last peak to RECOVERY (a new peak), or to
    the end of the data if never recovered - not merely to the last underwater bar."""
    peak = start_equity
    peak_ts = points[0].ts if points else None
    max_dd = ZERO
    max_dd_abs = ZERO
    dd_sum = ZERO
    under_sum = ZERO
    under_n = 0
    cur_bars = 0
    longest = timedelta(0)
    longest_bars = 0
    underwater = False
    for p in points:
        e = p.equity
        if e > peak:
            if underwater and peak_ts is not None:
                longest = max(longest, p.ts - peak_ts)  # recovery at p.ts
            peak, peak_ts, underwater, cur_bars = e, p.ts, False, 0
        dd = (peak - e) / peak if peak > 0 else ZERO
        dd_sum += dd
        if dd > max_dd:
            max_dd, max_dd_abs = dd, peak - e
        if e < peak:
            underwater = True
            under_sum += dd
            under_n += 1
            cur_bars += 1
            longest_bars = max(longest_bars, cur_bars)
    if underwater and peak_ts is not None and points:
        longest = max(longest, points[-1].ts - peak_ts)  # still underwater at the end
    n = len(points)
    hours = quantize(Decimal(int(longest.total_seconds())) / Decimal(3600), Decimal("0.1"))
    return {
        "max_drawdown_pct": _q(max_dd),
        "max_drawdown_abs": _q(max_dd_abs),
        "avg_drawdown_pct": _q(dd_sum / n) if n else NA("no equity observations"),
        "avg_underwater_depth_pct": _q(under_sum / under_n) if under_n else NA("never underwater"),
        "longest_drawdown_hours": str(hours),
        "longest_drawdown_bars": longest_bars,
        "recovered_at_end": not underwater,
    }


def _round_trips(res: RunResult, rules: dict[str, RuleProfile]) -> list[dict[str, Any]]:
    trips: list[dict[str, Any]] = []
    state: dict[str, dict[str, Any]] = {}
    for f in sorted(res.fills, key=lambda x: (x.ts, x.fill_id)):
        st = state.setdefault(f.symbol, {"pos": ZERO, "open": None})
        base_fee = f.fee_amount if f.side == Side.BUY and f.fee_asset != "USDT" else ZERO
        dust = rules[f.symbol].min_qty
        if f.side == Side.BUY:
            if st["open"] is None:
                st["open"] = {
                    "symbol": f.symbol,
                    "start": f.ts,
                    "pnl": ZERO,
                    "fees": ZERO,
                    "fills": 0,
                }
            st["pos"] += f.quantity - base_fee
        else:
            st["pos"] -= f.quantity
        tr = st["open"]
        if tr is not None:
            tr["fills"] += 1
            tr["fees"] += f.fee_amount * (f.price if f.fee_asset != "USDT" else ONE)
            if f.side == Side.SELL:
                tr["pnl"] += f.realized_pnl
                tr["end"] = f.ts
            if st["pos"] < dust and f.side == Side.SELL:
                tr["hold_hours"] = (tr["end"] - tr["start"]).total_seconds() / 3600
                trips.append(tr)
                st["open"] = None
    return trips


def compute_performance(
    res: RunResult, tf: Timeframe, rules: dict[str, RuleProfile], start_equity: Decimal
) -> PerformanceReport:
    rep = PerformanceReport()
    pts = res.equity
    rep.meta = {
        "status": res.status,
        "risk_verdict": res.risk_verdict,
        "bars_processed": res.bars_processed,
        "warmup_bars": res.warmup_bars,
        "reconciled": res.reconcile.ok,
        "labels": ["SIMULATED", "HISTORICAL DATA", "ESTIMATED FILLS", "NOT LIVE TRADING"],
    }
    if not pts:
        rep.returns = {"error": NA("no equity observations")}
        return rep
    end_eq = pts[-1].equity
    net = end_eq - start_equity
    fees_q = sum(
        (f.fee_amount * (f.price if f.fee_asset != "USDT" else ONE) for f in res.fills), ZERO
    )
    slip = sum((f.slippage_cost for f in res.fills), ZERO)
    days = (pts[-1].ts - pts[0].ts).total_seconds() / 86400
    dr = daily_returns(pts, start_equity)
    n_days = len(dr)

    # ----------------------------------------------------------- returns
    ann: Any
    if days >= MIN_DAYS_FOR_ANNUALISING and end_eq > 0:
        ann = _q((end_eq / start_equity) ** (Decimal(365) / Decimal(str(days))) - ONE)
    else:
        ann = NA(
            f"period {days:.0f} d < {MIN_DAYS_FOR_ANNUALISING} d: annualising is not meaningful"
        )
    monthly: dict[str, Decimal] = {}
    last_by_month: dict[str, Decimal] = {}
    for p in pts:
        d = _day(p.ts)
        last_by_month[f"{d.year}-{d.month:02d}"] = p.equity
    prev = start_equity
    for k in sorted(last_by_month):
        monthly[k] = _q(last_by_month[k] / prev - ONE)
        prev = last_by_month[k]
    rep.returns = {
        "starting_equity": start_equity,
        "ending_equity_marked": _q(end_eq),
        "ending_equity_liquidation_adjusted": _q(res.liquidation_equity),
        "total_net_pnl": _q(net),
        "gross_trading_pnl": _q(net + fees_q + slip),
        "total_fees_quote_equivalent": _q(fees_q),
        "total_slippage_cost": _q(slip),
        "net_return_pct": _q(net / start_equity),
        "annualized_return": ann,
        "monthly_returns": monthly,
        "daily_return_observations": n_days,
        "realized_pnl": _q(res.ledger.realized_total),
        "unrealized_pnl": _q(res.reconcile.unrealized),
        "note": "marked-to-market simulated equity; NOT realised cash profit",
    }

    # ----------------------------------------------------------- risk
    risk: dict[str, Any] = dict(drawdown_stats(pts, start_equity))
    if n_days >= MIN_DAYS_FOR_RATIOS:
        mean = sum(dr, ZERO) / n_days
        sd = _std(dr)
        downside = (sum((min(r, ZERO) ** 2 for r in dr), ZERO) / n_days).sqrt()
        risk["volatility_annualized"] = _q(sd * SQRT_365)
        risk["sharpe"] = _q(mean / sd * SQRT_365) if sd > 0 else NA("zero return variance")
        risk["sortino"] = _q(mean / downside * SQRT_365) if downside > 0 else NA("no downside days")
        risk["downside_deviation_daily"] = _q(downside)
    else:
        why = f"only {n_days} daily observations (< {MIN_DAYS_FOR_RATIOS})"
        for k in ("volatility_annualized", "sharpe", "sortino", "downside_deviation_daily"):
            risk[k] = NA(why)
    risk["worst_day_return"] = _q(min(dr)) if dr else NA("no daily observations")
    risk["days_loss_ge_1pct"] = sum(1 for r in dr if r <= Decimal("-0.01"))
    risk["days_loss_ge_2pct"] = sum(1 for r in dr if r <= Decimal("-0.02"))
    if n_days >= 20:
        srt = sorted(dr)
        risk["daily_return_p05"] = _q(srt[int(0.05 * (n_days - 1))])
    else:
        risk["daily_return_p05"] = NA("fewer than 20 daily observations")
    buckets: defaultdict[str, int] = defaultdict(int)
    for p in pts:
        ratio = p.exposure / p.equity if p.equity > 0 else ZERO
        key = (
            "flat(<0.1%)"
            if ratio < Decimal("0.001")
            else "0-10%"
            if ratio <= Decimal("0.10")
            else "10-20%"
            if ratio <= Decimal("0.20")
            else "20-30%"
            if ratio <= Decimal("0.30")
            else ">30%"
        )
        buckets[key] += 1
    risk["time_at_exposure"] = {k: _q(Decimal(v) / len(pts)) for k, v in sorted(buckets.items())}
    risk["risk_events"] = {
        k: sum(1 for e in res.risk_events if e.kind == k)
        for k in ("DAILY_LOSS_TRIP", "DRAWDOWN_HALT", "DAILY_RESET", "ORDER_REJECTED")
    }
    rep.risk = risk

    # ----------------------------------------------------------- trading
    trips = _round_trips(res, rules)
    wins = [t["pnl"] for t in trips if t["pnl"] > 0]
    losses = [t["pnl"] for t in trips if t["pnl"] < 0]
    notional = sum((f.notional for f in res.fills), ZERO)
    maker = [f for f in res.fills if f.liquidity == "MAKER"]
    trading: dict[str, Any] = {
        "closed_round_trips": len(trips),
        "fills": len(res.fills),
        "orders_total": len(res.orders),
        "orders_rejected": sum(1 for o in res.orders if o.status == OrderStatus.REJECTED),
        "orders_unfilled": sum(
            1
            for o in res.orders
            if o.filled_qty == 0 and o.status in (OrderStatus.EXPIRED, OrderStatus.CANCELLED)
        ),
        "rejections_by_reason": dict(sorted(res.rejections.items())),
        "turnover_x_start_equity": _q(notional / start_equity),
        "gross_traded_notional": _q(notional),
    }
    if trips:
        trading["win_rate"] = _q(Decimal(len(wins)) / len(trips))
        trading["avg_win"] = _q(sum(wins, ZERO) / len(wins)) if wins else NA("no winning trades")
        trading["avg_loss"] = (
            _q(sum(losses, ZERO) / len(losses)) if losses else NA("no losing trades")
        )
        trading["avg_net_pnl_per_trade"] = _q(sum((t["pnl"] for t in trips), ZERO) / len(trips))
        trading["profit_factor"] = (
            _q(sum(wins, ZERO) / abs(sum(losses, ZERO)))
            if wins and losses
            else NA("no losing trades" if wins else "no winning trades")
        )
        trading["avg_holding_hours"] = _q(
            Decimal(str(sum(t["hold_hours"] for t in trips) / len(trips)))
        )
    else:
        for k in (
            "win_rate",
            "avg_win",
            "avg_loss",
            "avg_net_pnl_per_trade",
            "profit_factor",
            "avg_holding_hours",
        ):
            trading[k] = NA("no closed round trips")
    gross = net + fees_q + slip
    trading["fee_to_gross_profit_ratio"] = (
        _q(fees_q / gross) if gross > 0 else NA("gross trading P&L is not positive")
    )
    if res.fills:
        trading["maker_fill_share"] = _q(Decimal(len(maker)) / len(res.fills))
        trading["taker_fill_share"] = _q(ONE - Decimal(len(maker)) / len(res.fills))
    else:
        trading["maker_fill_share"] = trading["taker_fill_share"] = NA("no fills")
    trading["maker_share_note"] = "ESTIMATED: candle-only data cannot support queue-position claims"
    rep.trading = trading

    # ----------------------------------------------------------- portfolio quality
    util = [p.exposure / p.equity for p in pts if p.equity > 0]
    held = [p.positions_value / p.equity for p in pts if p.equity > 0]
    by_asset: dict[str, Any] = {}
    values: dict[str, Decimal] = {}
    for base, pos in res.ledger.positions.items():
        sym = next((s for s in res.final_marks if s.startswith(base + "-")), None)
        mark = res.final_marks.get(sym or "", ZERO)
        val = pos.qty * mark
        values[base] = val
        by_asset[base] = {
            "final_qty": pos.qty,
            "final_value": _q(val),
            "unrealized_pnl": _q(val - pos.cost_total),
            "realized_pnl": _q(res.ledger.realized_by_asset.get(base, ZERO)),
            "contribution": _q(val - pos.cost_total + res.ledger.realized_by_asset.get(base, ZERO)),
        }
    tot = sum(values.values(), ZERO)
    rep.portfolio = {
        "avg_exposure_over_equity": _q(sum(util, ZERO) / len(util))
        if util
        else NA("no observations"),
        "avg_inventory_value_over_equity": _q(sum(held, ZERO) / len(held))
        if held
        else NA("no observations"),
        "max_exposure_over_equity": _q(max(util)) if util else NA("no observations"),
        "by_asset": by_asset,
        "unrealized_pnl_total": _q(res.reconcile.unrealized),
        "concentration_hhi": _q(sum(((v / tot) ** 2 for v in values.values()), ZERO))
        if tot > 0
        else NA("no inventory at end"),
        "max_asset_share": _q(max(values.values()) / tot) if tot > 0 else NA("no inventory at end"),
        "inventory_accumulation_note": "final inventory is marked at the last close, not liquidated",
    }
    return rep
