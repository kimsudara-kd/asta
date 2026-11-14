"""Deterministic dataset checksums (SHA-256 over canonical text)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal

from asta.research.data.models import Bar
from asta.research.types import SCHEMA_VERSION, Timeframe


def canon_decimal(d: Decimal) -> str:
    """Normalised plain-notation decimal so 1.50 and 1.5 hash identically."""
    n = d.normalize()
    text = format(n, "f")
    return "0" if text in ("-0", "") else text


def epoch_ms(ts: datetime) -> int:
    return int(ts.timestamp() * 1000)


def bar_line(b: Bar) -> str:
    return ",".join(
        (
            str(epoch_ms(b.open_time)),
            canon_decimal(b.open),
            canon_decimal(b.high),
            canon_decimal(b.low),
            canon_decimal(b.close),
            canon_decimal(b.volume),
        )
    )


def bars_checksum(venue: str, symbol: str, timeframe: Timeframe, bars: Iterable[Bar]) -> str:
    """Identity covers venue/symbol/timeframe/schema AND every row, in order."""
    h = hashlib.sha256()
    h.update(f"asta-bars|{SCHEMA_VERSION}|{venue}|{symbol}|{timeframe.value}\n".encode())
    for b in bars:
        h.update(bar_line(b).encode())
        h.update(b"\n")
    return h.hexdigest()
