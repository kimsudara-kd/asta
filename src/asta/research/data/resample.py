"""Derive coarser bars from a finer verified dataset. Never the other way round (ADR-0008).

A coarser bucket is emitted ONLY if every constituent finer bar is present; incomplete buckets are
dropped (they become gaps, which the quality engine then reports). No partial buckets, no
forward-fill, no fabricated volume.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from asta.research.data.importer import ImportResult, build_dataset
from asta.research.data.models import Bar, Dataset
from asta.research.types import Severity, SourceType, Timeframe


class ResampleError(Exception):
    pass


def resample(
    parent: Dataset, target: Timeframe, imported_at: datetime | None = None
) -> ImportResult:
    src = parent.meta.timeframe
    if target.seconds <= src.seconds:
        raise ResampleError(
            f"cannot derive {target.value} from {src.value}: finer or equal data is never invented"
        )
    if target.seconds % src.seconds != 0:
        raise ResampleError(f"{target.value} is not a whole multiple of {src.value}")
    if parent.meta.validation_status is None or parent.meta.validation_status == Severity.REJECTED:
        raise ResampleError("parent dataset is REJECTED or unvalidated; cannot derive from it")
    ratio = target.seconds // src.seconds
    step = src.seconds
    out: list[Bar] = []
    dropped = 0

    bucket: list[Bar] = []
    bucket_start: int | None = None

    def flush() -> None:
        nonlocal dropped, bucket, bucket_start
        if bucket_start is None:
            return
        complete = len(bucket) == ratio and all(
            int(b.open_time.timestamp()) == bucket_start + i * step for i, b in enumerate(bucket)
        )
        if complete:
            out.append(
                Bar(
                    bucket[0].open_time,
                    bucket[0].open,
                    max(b.high for b in bucket),
                    min(b.low for b in bucket),
                    bucket[-1].close,
                    sum((b.volume for b in bucket), Decimal(0)),
                )
            )
        else:
            dropped += 1
        bucket = []
        bucket_start = None

    for b in parent.bars:
        ts = int(b.open_time.timestamp())
        start = ts - (ts % target.seconds)
        if bucket_start is not None and start != bucket_start:
            flush()
        if bucket_start is None:
            bucket_start = start
        bucket.append(b)
    flush()

    root = parent.meta.provenance.get("root_source_type") or parent.meta.source_type.value
    prov = {
        "derived_from": parent.meta.checksum,
        "parent_timeframe": src.value,
        "method": "OHLCV aggregation of complete buckets only",
        "dropped_incomplete_buckets": dropped,
        "root_source_type": root,
        "parent_source": parent.meta.source,
    }
    return build_dataset(
        out,
        parent.meta.venue,
        parent.meta.symbol,
        target,
        f"derived:{parent.meta.source}",
        SourceType.DERIVED,
        prov,
        imported_at=imported_at or datetime.now(UTC),
        parent_checksums=(parent.meta.checksum,),
    )
