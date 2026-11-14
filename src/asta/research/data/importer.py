"""Offline import from LOCAL files only (CSV + JSON sidecar). No network, no exchange access.

Canonical CSV header: open_time,open,high,low,close,volume  (optional venue,symbol columns must
match the sidecar). `open_time` is epoch milliseconds (UTC by definition) or an ISO-8601 string
that MUST carry a UTC offset (naive timestamps are a REJECTED finding).
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from asta.core.timeutil import from_epoch_ms
from asta.research.data.checksum import bars_checksum
from asta.research.data.models import Bar, Dataset, DatasetMeta, QuoteTick, TradeTick
from asta.research.data.quality import QualityReport, validate_bars
from asta.research.types import SCHEMA_VERSION, Severity, SourceType, Timeframe

REQUIRED_COLUMNS = ("open_time", "open", "high", "low", "close", "volume")
MAX_FILE_BYTES = 512 * 1024 * 1024


class ImportRejected(Exception):
    """The file/sidecar cannot even be interpreted (distinct from a dataset that is REJECTED)."""


@dataclass(frozen=True)
class ImportResult:
    dataset: Dataset
    report: QualityReport


def _parse_ts(text: str) -> datetime:
    t = text.strip()
    if t.lstrip("-").isdigit():
        return from_epoch_ms(int(t))
    dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be timezone-aware UTC")
    return dt.astimezone(UTC)


def _dec(text: str) -> Decimal:
    try:
        d = Decimal(text.strip())
    except InvalidOperation as exc:
        raise ValueError(f"not a decimal: {text!r}") from exc
    if not d.is_finite():
        raise ValueError("non-finite decimal")
    return d


def parse_candles_csv(text: str, venue: str, symbol: str) -> tuple[list[Bar], list[str]]:
    """Parse rows; unparseable rows are reported (not skipped silently) -> dataset REJECTED."""
    reader = csv.DictReader(io.StringIO(text))
    cols = reader.fieldnames or []
    missing = [c for c in REQUIRED_COLUMNS if c not in cols]
    if missing:
        raise ImportRejected(f"missing columns: {missing}")
    bars: list[Bar] = []
    errors: list[str] = []
    for i, row in enumerate(reader, start=2):
        try:
            if "venue" in row and row["venue"] and row["venue"] != venue:
                raise ValueError(f"venue {row['venue']!r} != sidecar {venue!r}")
            if "symbol" in row and row["symbol"] and row["symbol"] != symbol:
                raise ValueError(f"symbol {row['symbol']!r} != sidecar {symbol!r}")
            bars.append(
                Bar(
                    _parse_ts(row["open_time"]),
                    _dec(row["open"]),
                    _dec(row["high"]),
                    _dec(row["low"]),
                    _dec(row["close"]),
                    _dec(row["volume"]),
                )
            )
        except (ValueError, KeyError, TypeError) as exc:
            errors.append(f"line {i}: {exc}")
    return bars, errors


def parse_trades_csv(text: str) -> list[TradeTick]:
    out: list[TradeTick] = []
    for row in csv.DictReader(io.StringIO(text)):
        out.append(
            TradeTick(
                _parse_ts(row["ts"]), _dec(row["price"]), _dec(row["quantity"]), row["trade_id"]
            )
        )
    return out


def parse_quotes_csv(text: str) -> list[QuoteTick]:
    out: list[QuoteTick] = []
    for row in csv.DictReader(io.StringIO(text)):
        out.append(
            QuoteTick(
                _parse_ts(row["ts"]),
                _dec(row["bid"]),
                _dec(row["ask"]),
                _dec(row["bid_qty"]),
                _dec(row["ask_qty"]),
            )
        )
    return out


def read_sidecar(path: Path) -> dict[str, Any]:
    side = path.with_name(path.name + ".meta.json")
    if not side.is_file():
        raise ImportRejected(
            f"sidecar {side.name} is required (source, source_type, venue, symbol, timeframe)"
        )
    data = json.loads(side.read_text(encoding="utf-8"))
    for k in ("venue", "symbol", "timeframe", "source", "source_type"):
        if k not in data:
            raise ImportRejected(f"sidecar missing {k!r}")
    return dict(data)


def build_dataset(
    bars: Sequence[Bar],
    venue: str,
    symbol: str,
    timeframe: Timeframe,
    source: str,
    source_type: SourceType,
    provenance: dict[str, Any],
    parse_errors: Sequence[str] = (),
    tick_size: Decimal | None = None,
    imported_at: datetime | None = None,
    parent_checksums: tuple[str, ...] = (),
) -> ImportResult:
    """Validate + stamp metadata. A REJECTED dataset is still returned (diagnostics) but flagged."""
    report = validate_bars(bars, timeframe, venue, symbol, tick_size, parse_errors)
    ordered = tuple(bars)
    checksum = bars_checksum(venue, symbol, timeframe, ordered)
    start = ordered[0].open_time if ordered else datetime(1970, 1, 1, tzinfo=UTC)
    end = (ordered[-1].open_time + timedelta(seconds=timeframe.seconds)) if ordered else start
    prov = dict(provenance)
    if source_type in (SourceType.FIXTURE, SourceType.SYNTHETIC):
        prov.setdefault("root_source_type", source_type.value)
    meta = DatasetMeta(
        venue=venue,
        symbol=symbol,
        timeframe=timeframe,
        source=source,
        source_type=source_type,
        schema_version=SCHEMA_VERSION,
        checksum=checksum,
        row_count=len(ordered),
        period_start=start,
        period_end=end,
        imported_at=imported_at or datetime.now(UTC),
        provenance=prov,
        parent_checksums=parent_checksums,
        validation_status=report.status,
    )
    return ImportResult(Dataset(meta, ordered), report)


def import_candles_file(path: Path, tick_size: Decimal | None = None) -> ImportResult:
    """Import one local CSV (+ sidecar). Never opens a socket; refuses oversized files."""
    if not path.is_file():
        raise ImportRejected(f"file not found: {path.name}")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ImportRejected("file too large")
    side = read_sidecar(path)
    try:
        timeframe = Timeframe(side["timeframe"])
        source_type = SourceType(side["source_type"])
    except ValueError as exc:
        raise ImportRejected(str(exc)) from exc
    if source_type == SourceType.DERIVED:
        raise ImportRejected("DERIVED datasets are produced by resample(), not imported")
    text = path.read_text(encoding="utf-8")
    bars, errors = parse_candles_csv(text, side["venue"], side["symbol"])
    prov: dict[str, Any] = dict(side.get("provenance") or {})
    prov["file_name"] = path.name
    return build_dataset(
        bars,
        side["venue"],
        side["symbol"],
        timeframe,
        str(side["source"]),
        source_type,
        prov,
        errors,
        tick_size,
    )


def severity_of(result: ImportResult) -> Severity:
    return result.report.status
