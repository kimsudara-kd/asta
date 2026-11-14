"""Content-addressed local dataset store: `<checksum>.csv` + `<checksum>.meta.json`.

Files are immutable (named by checksum) and re-verified on every load, so a run can only ever read
the exact bytes it was configured with. This is a LOCAL file store: no network.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from asta.core.timeutil import from_epoch_ms
from asta.research.data.checksum import bar_line, bars_checksum
from asta.research.data.models import Bar, Dataset, DatasetMeta
from asta.research.types import Severity, SourceType, Timeframe

HEADER = "open_time,open,high,low,close,volume\n"


class StoreError(Exception):
    pass


class DatasetFileStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def _csv(self, checksum: str) -> Path:
        if len(checksum) != 64 or not all(c in "0123456789abcdef" for c in checksum):
            raise StoreError("invalid checksum")
        return self.root / f"{checksum}.csv"

    def exists(self, checksum: str) -> bool:
        return self._csv(checksum).is_file()

    def save(self, ds: Dataset) -> Path:
        path = self._csv(ds.meta.checksum)
        if path.exists():
            return path  # content-addressed: identical by construction (verified on load)
        body = HEADER + "".join(bar_line(b) + "\n" for b in ds.bars)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, path)
        meta = {
            "venue": ds.meta.venue,
            "symbol": ds.meta.symbol,
            "timeframe": ds.meta.timeframe.value,
            "source": ds.meta.source,
            "source_type": ds.meta.source_type.value,
            "schema_version": ds.meta.schema_version,
            "checksum": ds.meta.checksum,
            "row_count": ds.meta.row_count,
            "imported_at": ds.meta.imported_at.isoformat(),
            "provenance": ds.meta.provenance,
            "parent_checksums": list(ds.meta.parent_checksums),
            "validation_status": ds.meta.validation_status.value
            if ds.meta.validation_status
            else None,
        }
        path.with_suffix(".meta.json").write_text(
            json.dumps(meta, sort_keys=True), encoding="utf-8"
        )
        return path

    def load(self, checksum: str) -> Dataset:
        path = self._csv(checksum)
        if not path.is_file():
            raise StoreError(f"dataset {checksum[:12]} not in store")
        meta_raw: dict[str, Any] = json.loads(
            path.with_suffix(".meta.json").read_text(encoding="utf-8")
        )
        tf = Timeframe(meta_raw["timeframe"])
        bars: list[Bar] = []
        lines = path.read_text(encoding="utf-8").splitlines()
        if not lines or lines[0] + "\n" != HEADER:
            raise StoreError("bad header")
        for line in lines[1:]:
            ts, o, h, lo, c, v = line.split(",")
            bars.append(
                Bar(
                    from_epoch_ms(int(ts)),
                    Decimal(o),
                    Decimal(h),
                    Decimal(lo),
                    Decimal(c),
                    Decimal(v),
                )
            )
        actual = bars_checksum(meta_raw["venue"], meta_raw["symbol"], tf, bars)
        if actual != checksum:
            raise StoreError(f"checksum mismatch: stored file does not hash to {checksum[:12]}")
        start = bars[0].open_time if bars else datetime(1970, 1, 1, tzinfo=UTC)
        from datetime import timedelta

        end = bars[-1].open_time + timedelta(seconds=tf.seconds) if bars else start
        vs = meta_raw.get("validation_status")
        meta = DatasetMeta(
            venue=meta_raw["venue"],
            symbol=meta_raw["symbol"],
            timeframe=tf,
            source=meta_raw["source"],
            source_type=SourceType(meta_raw["source_type"]),
            schema_version=meta_raw["schema_version"],
            checksum=checksum,
            row_count=len(bars),
            period_start=start,
            period_end=end,
            imported_at=datetime.fromisoformat(meta_raw["imported_at"]),
            provenance=dict(meta_raw.get("provenance") or {}),
            parent_checksums=tuple(meta_raw.get("parent_checksums") or ()),
            validation_status=Severity(vs) if vs else None,
        )
        return Dataset(meta, tuple(bars))
