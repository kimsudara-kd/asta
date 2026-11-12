"""Strictly causal, incremental indicators (Decimal). `update(bar)` may only use that bar and the
past; there is no way to ask an indicator about the future. Each returns None until warmed up."""

from __future__ import annotations

from collections import deque
from decimal import Decimal

from asta.research.data.models import Bar

ZERO = Decimal(0)
ONE = Decimal(1)


class SMA:
    def __init__(self, period: int) -> None:
        if period < 1:
            raise ValueError("period must be >= 1")
        self.period = period
        self._buf: deque[Decimal] = deque(maxlen=period)
        self._sum = ZERO
        self.value: Decimal | None = None

    def update(self, x: Decimal) -> Decimal | None:
        if len(self._buf) == self.period:
            self._sum -= self._buf[0]
        self._buf.append(x)
        self._sum += x
        self.value = self._sum / self.period if len(self._buf) == self.period else None
        return self.value


class EMA:
    """Seeded with the SMA of the first `period` values, then alpha = 2/(period+1)."""

    def __init__(self, period: int) -> None:
        if period < 1:
            raise ValueError("period must be >= 1")
        self.period = period
        self.alpha = Decimal(2) / Decimal(period + 1)
        self._seed: list[Decimal] = []
        self.value: Decimal | None = None
        self.prev: Decimal | None = None  # value one update ago (slope)

    def update(self, x: Decimal) -> Decimal | None:
        self.prev = self.value
        if self.value is None:
            self._seed.append(x)
            if len(self._seed) == self.period:
                self.value = sum(self._seed, ZERO) / self.period
                self._seed = []
        else:
            self.value = self.alpha * x + (ONE - self.alpha) * self.value
        return self.value


class ATR:
    """Wilder's average true range (needs the previous close, which is past data)."""

    def __init__(self, period: int) -> None:
        self.period = period
        self._prev_close: Decimal | None = None
        self._trs: list[Decimal] = []
        self.value: Decimal | None = None

    def update(self, bar: Bar) -> Decimal | None:
        if self._prev_close is None:
            tr = bar.high - bar.low
        else:
            tr = max(
                bar.high - bar.low,
                abs(bar.high - self._prev_close),
                abs(bar.low - self._prev_close),
            )
        self._prev_close = bar.close
        if self.value is None:
            self._trs.append(tr)
            if len(self._trs) == self.period:
                self.value = sum(self._trs, ZERO) / self.period
                self._trs = []
        else:
            self.value = (self.value * (self.period - 1) + tr) / self.period
        return self.value


class ReturnVol:
    """Standard deviation of simple close-to-close returns over `period` bars."""

    def __init__(self, period: int) -> None:
        self.period = period
        self._rets: deque[Decimal] = deque(maxlen=period)
        self._prev: Decimal | None = None
        self.value: Decimal | None = None

    def update(self, close: Decimal) -> Decimal | None:
        if self._prev is not None and self._prev > 0:
            self._rets.append(close / self._prev - ONE)
        self._prev = close
        if len(self._rets) == self.period:
            mean = sum(self._rets, ZERO) / self.period
            var = sum(((r - mean) ** 2 for r in self._rets), ZERO) / self.period
            self.value = var.sqrt()
        else:
            self.value = None
        return self.value


class RollingMax:
    def __init__(self, period: int) -> None:
        self._buf: deque[Decimal] = deque(maxlen=period)
        self.period = period

    def update(self, x: Decimal) -> Decimal | None:
        self._buf.append(x)
        return max(self._buf) if len(self._buf) == self.period else None
