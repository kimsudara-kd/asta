"""Property-based tests (Hypothesis): the accounting invariants must hold for ANY action sequence."""

from decimal import Decimal as D
from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from asta.core.money import round_down, round_up
from asta.research.data.fixtures import DOWNTREND, HIGHVOL, RANGE, UPTREND, generate_bars, seg
from asta.research.sim.context import StrategyContext
from asta.research.sim.orders import CancelIntent, OrderIntent
from asta.research.strategies.base import Action, Strategy
from asta.research.types import OrderType, Side, Timeframe

from .engine_helpers import FEES, RULES, _NoParams, run
from .helpers import T0

PLAN = st.lists(
    st.tuples(st.integers(0, 6), st.integers(0, 9), st.integers(1, 6)), min_size=20, max_size=40
)
SEGMENTS = st.sampled_from([UPTREND, DOWNTREND, RANGE, HIGHVOL])


class Fuzz(Strategy):
    """Drives the engine with arbitrary (but deterministic) intents derived from a plan."""

    strategy_id = "fuzz"
    version = "0"

    def __init__(self, plan: list[tuple[int, int, int]]) -> None:
        super().__init__(_NoParams())
        self.plan = plan
        self.n = 0
        self.ids: list[str] = []

    def params_json(self) -> dict[str, object]:
        return {}

    def on_bar(self, ctx: StrategyContext) -> list[Action]:
        if ctx.index < 0 or ctx.index >= len(self.plan):
            return []
        kind, x, y = self.plan[ctx.index]
        sym = "BTC-USDT"
        if not ctx.has_bar(sym):
            return []
        close = ctx.bars(sym)[-1].close
        pos = ctx.position(sym).qty
        self.n += 1
        cid = f"f{self.n}"
        out: list[Action] = []
        if kind == 1:
            out.append(
                OrderIntent(
                    sym, Side.BUY, OrderType.MARKET, quote_notional=D(10 + x * 15), client_id=cid
                )
            )
        elif kind == 2:
            px = close * (1 - D(y) / 100)
            out.append(
                OrderIntent(
                    sym,
                    Side.BUY,
                    OrderType.LIMIT,
                    quantity=D("0.05") * (1 + x),
                    limit_price=px,
                    client_id=cid,
                    expire_after_bars=1 + x % 5,
                )
            )
        elif kind == 3 and pos > 0:
            out.append(
                OrderIntent(
                    sym, Side.SELL, OrderType.MARKET, quantity=pos * D(x + 1) / 10, client_id=cid
                )
            )
        elif kind == 4 and pos > 0:
            out.append(
                OrderIntent(
                    sym,
                    Side.SELL,
                    OrderType.LIMIT,
                    quantity=pos * D(x + 1) / 10,
                    limit_price=close * (1 + D(y) / 100),
                    client_id=cid,
                )
            )
        elif kind == 5 and pos > 0:
            out.append(
                OrderIntent(
                    sym,
                    Side.SELL,
                    OrderType.STOP_MARKET,
                    quantity=pos * D(x + 1) / 10,
                    stop_price=close * (1 - D(y) / 100),
                    client_id=cid,
                )
            )
        elif kind == 6 and self.ids:
            out.append(CancelIntent(self.ids[x % len(self.ids)]))
        self.ids.append(cid)
        return out


def make_bars(segs, seed):
    return generate_bars(T0, Timeframe.H1, [seg(s, 8) for s in segs], seed, D("100"))


COMMON: dict[str, Any] = dict(
    max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)


@settings(**COMMON)
@given(PLAN, st.lists(SEGMENTS, min_size=3, max_size=5), st.integers(0, 10_000))
def test_ledger_invariants_hold_for_any_action_sequence(plan, segs, seed):
    bars = make_bars(segs, seed)
    res = run(bars, {}, strategy=Fuzz(plan))
    # 1. reconciliation (balance preservation + equity identity, exact Decimal)
    assert res.reconcile.ok, res.reconcile.issues
    # 2. replay the entries: at every operation boundary balances are non-negative and reservations
    #    are within [0, balance]. A fill (its entries and the release it triggers share a fill_id)
    #    is ONE atomic operation, so invariants are checked after the whole group.
    bal: dict[str, D] = {}
    resv: dict[str, D] = {}
    entries = res.ledger.entries
    for i, e in enumerate(entries):
        if e.kind == "RESERVE":
            resv[e.asset] = resv.get(e.asset, D(0)) + e.amount
        elif e.kind == "RELEASE":
            resv[e.asset] = resv.get(e.asset, D(0)) - e.amount
        else:
            bal[e.asset] = bal.get(e.asset, D(0)) + e.amount
        nxt = entries[i + 1] if i + 1 < len(entries) else None
        if e.fill_id is not None and nxt is not None and nxt.fill_id == e.fill_id:
            continue  # still inside one atomic fill
        for a, v in bal.items():
            assert v >= 0, f"negative balance {a} at seq {e.seq}"
        for a, v in resv.items():
            assert 0 <= v <= bal.get(a, D(0)), f"reservation invalid {a} at seq {e.seq}"
    # 3. no spot inventory below zero at the end, nothing left reserved after end-of-data cancel
    assert res.ledger.balance("BTC") >= 0 and res.ledger.reserved("BTC") == 0
    assert res.ledger.reserved("USDT") == 0
    # 4. every order ended terminal; fills sum to order filled quantity
    assert all(o.is_terminal for o in res.orders)
    for o in res.orders:
        assert (
            sum((f.quantity for f in res.fills if f.order_id == o.order_id), D(0)) == o.filled_qty
        )


@settings(**COMMON)
@given(PLAN, st.lists(SEGMENTS, min_size=3, max_size=5), st.integers(0, 10_000))
def test_fee_and_fill_price_arithmetic(plan, segs, seed):
    bars = make_bars(segs, seed)
    by_ts = {b.open_time: b for b in bars}
    res = run(bars, {}, strategy=Fuzz(plan))
    for f in res.fills:
        bps = FEES.maker_bps if f.liquidity == "MAKER" else FEES.taker_bps
        if f.side == Side.BUY:
            assert f.notional == round_up(f.price * f.quantity)
            assert f.fee_asset == "BTC" and f.fee_amount == round_up(f.quantity * bps / D(10_000))
        else:
            assert f.notional == round_down(f.price * f.quantity)
            assert f.fee_asset == "USDT" and f.fee_amount == round_up(f.notional * bps / D(10_000))
        b = by_ts[f.ts]
        assert b.low <= f.price <= b.high  # a fill can never be outside the bar's traded range
        assert f.fee_amount >= 0 and f.slippage_cost >= 0
        assert f.quantity % RULES.step_size == 0 and f.price % RULES.tick_size == 0


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(PLAN, st.lists(SEGMENTS, min_size=3, max_size=4), st.integers(0, 10_000))
def test_equity_and_results_are_reproducible(plan, segs, seed):
    bars = make_bars(segs, seed)
    a = run(bars, {}, strategy=Fuzz(plan), checkpoint_every=5)
    b = run(bars, {}, strategy=Fuzz(plan), checkpoint_every=5)
    assert [(p.ts, p.equity, p.cash) for p in a.equity] == [
        (p.ts, p.equity, p.cash) for p in b.equity
    ]
    assert [(f.fill_id, f.price, f.quantity) for f in a.fills] == [
        (f.fill_id, f.price, f.quantity) for f in b.fills
    ]
    assert a.ledger.entries == b.ledger.entries
    assert [c.digest for c in a.checkpoints] == [c.digest for c in b.checkpoints]


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.integers(1, 6), st.lists(SEGMENTS, min_size=2, max_size=3), st.integers(0, 1000))
def test_resubmitting_the_same_client_id_never_creates_a_second_order(repeats, segs, seed):
    bars = make_bars(segs, seed)
    script = {
        i: [
            OrderIntent(
                "BTC-USDT", Side.BUY, OrderType.MARKET, quote_notional=D(50), client_id="dup"
            )
        ]
        for i in range(repeats)
    }
    res = run(bars, script)
    assert len(res.orders) == 1 and res.counters["duplicate_ignored"] == repeats - 1
    assert len([f for f in res.fills if f.order_id == 1]) <= 1 + len(bars)


@settings(**COMMON)
@given(PLAN, st.lists(SEGMENTS, min_size=3, max_size=5), st.integers(0, 10_000))
def test_no_short_selling_or_margin_is_reachable(plan, segs, seed):
    """Whatever the strategy asks, cumulative sold quantity never exceeds cumulative bought."""
    bars = make_bars(segs, seed)
    res = run(bars, {}, strategy=Fuzz(plan))
    bought = sum((f.quantity for f in res.fills if f.side == Side.BUY), D(0))
    sold = sum((f.quantity for f in res.fills if f.side == Side.SELL), D(0))
    assert sold <= bought
    running = D(0)
    for f in res.fills:
        running += f.quantity if f.side == Side.BUY else -f.quantity
        assert running >= 0
