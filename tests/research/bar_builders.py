"""Build hand-designed bar series from close prices (open = previous close)."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal as D

from asta.research.data.models import Bar
from asta.research.types import Timeframe

from .helpers import bar


def from_closes(
    closes: Sequence[str | float | int],
    wick: str = "0.5",
    vol: str = "1000",
    start: str | None = None,
    tf: Timeframe = Timeframe.H1,
    first_index: int = 0,
) -> list[Bar]:
    out: list[Bar] = []
    prev = D(str(start if start is not None else closes[0]))
    for i, raw in enumerate(closes):
        c = D(str(raw))
        hi = max(prev, c) + D(wick)
        lo = min(prev, c) - D(wick)
        out.append(bar(first_index + i, str(prev), str(hi), str(lo), str(c), vol, tf))
        prev = c
    return out


def ramp(start: float, step: float, n: int) -> list[float]:
    return [round(start + step * i, 2) for i in range(n)]
