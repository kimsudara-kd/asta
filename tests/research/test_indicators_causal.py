"""Indicators are strictly causal: the value after n updates never depends on later inputs."""

from decimal import Decimal as D

from hypothesis import given, settings
from hypothesis import strategies as st

from asta.research.data.fixtures import UPTREND, generate_bars, seg
from asta.research.strategies.indicators import ATR, EMA, SMA, ReturnVol, RollingMax
from asta.research.types import Timeframe

from .helpers import T0


def test_sma_known_values_and_warmup():
    sma = SMA(3)
    assert [sma.update(D(x)) for x in (1, 2, 3, 4, 5)] == [None, None, D(2), D(3), D(4)]


def test_ema_known_values_seeded_with_sma():
    ema = EMA(3)  # alpha = 0.5
    vals = [ema.update(D(x)) for x in (2, 4, 6, 8, 10)]
    assert vals[:2] == [None, None]
    assert vals[2] == D(4)  # SMA seed (2+4+6)/3
    assert vals[3] == D("0.5") * 8 + D("0.5") * 4 == D(6)
    assert vals[4] == D(8)
    assert ema.prev == D(6)


def test_atr_wilder_known_values():
    from asta.research.data.models import Bar

    def b(i, o, h, lo, c):
        return Bar(T0.replace(hour=i), D(o), D(h), D(lo), D(c), D(1))

    atr = ATR(2)
    assert atr.update(b(0, "10", "12", "9", "11")) is None  # TR = 3
    assert atr.update(b(1, "11", "13", "10", "12")) == D(
        "3"
    )  # TR = max(3, |13-11|, |10-11|) = 3 -> (3+3)/2
    # next TR = max(h-l=4, |14-12|=2, |10-12|=2) = 4 ; Wilder: (3*(2-1) + 4)/2 = 3.5
    assert atr.update(b(2, "12", "14", "10", "13")) == D("3.5")


def test_return_vol_and_rolling_max():
    v = ReturnVol(3)
    out = [v.update(D(x)) for x in (100, 110, 99, 108.9, 98.01)]
    assert out[:3] == [None, None, None] and out[3] is not None and out[3] > 0
    r = RollingMax(2)
    assert [r.update(D(x)) for x in (1, 5, 3, 2)] == [None, D(5), D(5), D(3)]


@settings(max_examples=40, deadline=None)
@given(st.integers(0, 500), st.integers(5, 60), st.integers(2, 12))
def test_prefix_invariance_for_every_indicator(seed, cut, period):
    """Value at index i from a run over the first i+1 bars == value at i from a run over ALL bars."""
    bars = generate_bars(T0, Timeframe.H1, [seg(UPTREND, 80)], seed, D("100"))
    if cut >= len(bars):
        return
    for make, feed in (
        (lambda: SMA(period), lambda ind, b: ind.update(b.close)),
        (lambda: EMA(period), lambda ind, b: ind.update(b.close)),
        (lambda: ATR(period), lambda ind, b: ind.update(b)),
        (lambda: ReturnVol(period), lambda ind, b: ind.update(b.close)),
        (lambda: RollingMax(period), lambda ind, b: ind.update(b.close)),
    ):
        full, part = make(), make()
        full_vals = [feed(full, b) for b in bars]
        part_vals = [feed(part, b) for b in bars[: cut + 1]]
        assert full_vals[cut] == part_vals[cut]
        # and corrupting the future cannot change the past
        mutated = list(bars[: cut + 1]) + [
            type(b)(b.open_time, b.open * 7, b.high * 9, b.low, b.close * 5, b.volume)
            for b in bars[cut + 1 :]
        ]
        other = make()
        other_vals = [feed(other, b) for b in mutated]
        assert other_vals[: cut + 1] == full_vals[: cut + 1]
