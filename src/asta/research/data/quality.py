"""Data-quality engine: PASS / WARNING / REJECTED (ADR-0008).

Missing-candle policy: gaps are REPORTED and never filled. <= 0.5 % missing bars with no run of
more than 12 consecutive missing bars is a WARNING; anything worse is REJECTED. Fabricating prices
or volume is never done here or anywhere else in the engine.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from asta.core.events import Venue
from asta.research.data.models import Bar, QuoteTick, TradeTick
from asta.research.types import Severity, Timeframe

MAX_MISSING_FRACTION = Decimal("0.005")
MAX_MISSING_RUN = 12
DISCONTINUITY_PCT = Decimal("0.20")
MAX_EXAMPLES = 5


@dataclass(frozen=True)
class QualityFinding:
    code: str
    severity: Severity
    message: str
    count: int = 1
    examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class QualityReport:
    status: Severity
    findings: tuple[QualityFinding, ...]
    stats: dict[str, Any] = field(default_factory=dict)

    def has(self, code: str) -> bool:
        return any(f.code == code for f in self.findings)

    def finding(self, code: str) -> QualityFinding | None:
        return next((f for f in self.findings if f.code == code), None)

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "findings": [
                {
                    "code": f.code,
                    "severity": f.severity.value,
                    "message": f.message,
                    "count": f.count,
                    "examples": list(f.examples),
                }
                for f in self.findings
            ],
            "stats": self.stats,
        }


def _worst(findings: Sequence[QualityFinding]) -> Severity:
    worst = Severity.PASS
    for f in findings:
        if f.severity.rank > worst.rank:
            worst = f.severity
    return worst


def validate_bars(
    bars: Sequence[Bar],
    timeframe: Timeframe,
    venue: str,
    symbol: str,
    tick_size: Decimal | None = None,
    parse_errors: Sequence[str] = (),
) -> QualityReport:
    f: list[QualityFinding] = []
    stats: dict[str, Any] = {"row_count": len(bars)}

    # identity
    if venue not in {v.value for v in Venue}:
        f.append(QualityFinding("VENUE_UNKNOWN", Severity.REJECTED, f"unknown venue {venue!r}"))
    parts = symbol.split("-")
    if len(parts) != 2 or not all(p.isalnum() and p.isupper() for p in parts):
        f.append(
            QualityFinding(
                "SYMBOL_INVALID", Severity.REJECTED, f"symbol {symbol!r} is not BASE-QUOTE"
            )
        )
    if parse_errors:
        f.append(
            QualityFinding(
                "PARSE_ERROR",
                Severity.REJECTED,
                "rows could not be parsed",
                len(parse_errors),
                tuple(parse_errors[:MAX_EXAMPLES]),
            )
        )
    if not bars:
        f.append(QualityFinding("EMPTY", Severity.REJECTED, "dataset has no rows"))
        return QualityReport(_worst(f), tuple(f), stats)

    tf_s = timeframe.seconds
    misaligned: list[str] = []
    dup: list[str] = []
    nonmono: list[str] = []
    nonpos: list[str] = []
    negvol: list[str] = []
    zerovol = 0
    ohlc: list[str] = []
    jumps: list[str] = []
    off_tick: list[str] = []
    missing_total = 0
    max_run = 0
    gap_examples: list[str] = []

    prev: Bar | None = None
    for b in bars:
        ts = int(b.open_time.timestamp())
        if ts % tf_s != 0:
            misaligned.append(b.open_time.isoformat())
        if b.open <= 0 or b.high <= 0 or b.low <= 0 or b.close <= 0:
            nonpos.append(b.open_time.isoformat())
        if b.volume < 0:
            negvol.append(b.open_time.isoformat())
        elif b.volume == 0:
            zerovol += 1
        if b.high < max(b.open, b.close, b.low) or b.low > min(b.open, b.close, b.high):
            ohlc.append(b.open_time.isoformat())
        if tick_size is not None and tick_size > 0:
            for px in (b.open, b.high, b.low, b.close):
                if px % tick_size != 0:
                    off_tick.append(b.open_time.isoformat())
                    break
        if prev is not None:
            pts = int(prev.open_time.timestamp())
            if ts == pts:
                dup.append(b.open_time.isoformat())
            elif ts < pts:
                nonmono.append(b.open_time.isoformat())
            else:
                step = (ts - pts) // tf_s
                if step > 1:
                    run = step - 1
                    missing_total += run
                    max_run = max(max_run, run)
                    if len(gap_examples) < MAX_EXAMPLES:
                        gap_examples.append(f"{prev.open_time.isoformat()} +{run} missing")
                if prev.close > 0 and abs(b.open - prev.close) / prev.close > DISCONTINUITY_PCT:
                    jumps.append(b.open_time.isoformat())
        prev = b

    def add(
        code: str, sev: Severity, msg: str, ex: Sequence[str], count: int | None = None
    ) -> None:
        f.append(
            QualityFinding(
                code, sev, msg, len(ex) if count is None else count, tuple(ex[:MAX_EXAMPLES])
            )
        )

    if misaligned:
        add(
            "NOT_ALIGNED",
            Severity.REJECTED,
            f"open_time not aligned to {timeframe.value}",
            misaligned,
        )
    if dup:
        add("DUPLICATE", Severity.REJECTED, "duplicate candles", dup)
    if nonmono:
        add("NON_MONOTONIC", Severity.REJECTED, "timestamps go backwards", nonmono)
    if nonpos:
        add("NON_POSITIVE_PRICE", Severity.REJECTED, "zero or negative price", nonpos)
    if negvol:
        add("NEGATIVE_VOLUME", Severity.REJECTED, "negative volume", negvol)
    if ohlc:
        add("OHLC_INCONSISTENT", Severity.REJECTED, "high/low inconsistent with open/close", ohlc)
    if zerovol:
        f.append(QualityFinding("ZERO_VOLUME", Severity.WARNING, "bars with zero volume", zerovol))
    if jumps:
        add(
            "DISCONTINUITY",
            Severity.WARNING,
            f"open differs from previous close by > {DISCONTINUITY_PCT:.0%}",
            jumps,
        )
    if off_tick:
        add(
            "PRECISION_TICK",
            Severity.WARNING,
            "prices not multiples of the (unverified historical) tick size",
            off_tick,
        )
    first = bars[0].open_time
    last = bars[-1].open_time
    expected = int((last.timestamp() - first.timestamp()) // tf_s) + 1 if not nonmono else len(bars)
    stats.update(
        {
            "first": first.isoformat(),
            "last": last.isoformat(),
            "expected_rows": expected,
            "missing_rows": missing_total,
            "max_missing_run": max_run,
            "zero_volume_rows": zerovol,
        }
    )
    if missing_total:
        frac = Decimal(missing_total) / Decimal(max(expected, 1))
        stats["missing_fraction"] = str(frac.quantize(Decimal("0.000001")))
        sev = (
            Severity.WARNING
            if frac <= MAX_MISSING_FRACTION and max_run <= MAX_MISSING_RUN
            else Severity.REJECTED
        )
        f.append(
            QualityFinding(
                "GAPS",
                sev,
                f"{missing_total} missing bars (max run {max_run}); never filled or interpolated",
                missing_total,
                tuple(gap_examples),
            )
        )
    status = _worst(f)
    return QualityReport(status, tuple(f), stats)


def validate_trades(trades: Sequence[TradeTick]) -> QualityReport:
    f: list[QualityFinding] = []
    if not trades:
        f.append(QualityFinding("EMPTY", Severity.REJECTED, "no trades"))
        return QualityReport(Severity.REJECTED, tuple(f), {"row_count": 0})
    seen: set[str] = set()
    dup = nonmono = bad = 0
    prev = trades[0].ts
    for t in trades:
        if t.trade_id in seen:
            dup += 1
        seen.add(t.trade_id)
        if t.ts < prev:
            nonmono += 1
        prev = t.ts
        if t.price <= 0 or t.quantity <= 0:
            bad += 1
    if dup:
        f.append(QualityFinding("DUPLICATE", Severity.REJECTED, "duplicate trade ids", dup))
    if nonmono:
        f.append(
            QualityFinding(
                "NON_MONOTONIC", Severity.REJECTED, "trade timestamps go backwards", nonmono
            )
        )
    if bad:
        f.append(
            QualityFinding("INVALID_VALUES", Severity.REJECTED, "non-positive price/quantity", bad)
        )
    return QualityReport(_worst(f), tuple(f), {"row_count": len(trades)})


def validate_quotes(quotes: Sequence[QuoteTick]) -> QualityReport:
    f: list[QualityFinding] = []
    if not quotes:
        f.append(QualityFinding("EMPTY", Severity.REJECTED, "no quotes"))
        return QualityReport(Severity.REJECTED, tuple(f), {"row_count": 0})
    nonmono = crossed = bad = 0
    prev = quotes[0].ts
    for q in quotes:
        if q.ts < prev:
            nonmono += 1
        prev = q.ts
        if q.bid <= 0 or q.ask <= 0 or q.bid_qty < 0 or q.ask_qty < 0:
            bad += 1
        elif q.bid > q.ask:
            crossed += 1
    if nonmono:
        f.append(
            QualityFinding(
                "NON_MONOTONIC", Severity.REJECTED, "quote timestamps go backwards", nonmono
            )
        )
    if crossed:
        f.append(QualityFinding("CROSSED_BOOK", Severity.REJECTED, "bid > ask", crossed))
    if bad:
        f.append(
            QualityFinding("INVALID_VALUES", Severity.REJECTED, "non-positive price/size", bad)
        )
    return QualityReport(_worst(f), tuple(f), {"row_count": len(quotes)})
