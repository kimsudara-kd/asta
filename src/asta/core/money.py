"""Decimal-only financial arithmetic helpers (ADR-0003)."""

from __future__ import annotations

import json
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any

_ZERO = Decimal(0)


def to_decimal(value: Any) -> Decimal:
    """Convert str/int/Decimal to Decimal. Floats are refused: they silently lose precision."""
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError(f"refusing to convert {type(value).__name__} to Decimal; use str")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int | str):
        try:
            result = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"not a decimal: {value!r}") from exc
    else:
        raise TypeError(f"cannot convert {type(value).__name__} to Decimal")
    if not result.is_finite():
        raise ValueError("non-finite decimal")
    return result


def floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    """Round DOWN to a multiple of `step` (never rounds a size or price upward)."""
    if step <= _ZERO:
        raise ValueError("step must be positive")
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def loads_decimal(text: str | bytes) -> Any:
    """JSON-decode with every JSON number parsed as Decimal (never float)."""
    return json.loads(text, parse_float=Decimal, parse_int=int)
