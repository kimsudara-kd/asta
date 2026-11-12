"""Download PUBLIC Binance klines into local files with provenance (research data acquisition).

This is a SEPARATE tool, not part of the backtest engine. It uses the existing M0 egress-checked
public client (allow-listed host `data-api.binance.vision`, no keys, no private paths) and writes
canonical CSV + JSON sidecar that the offline importer reads. OKX cannot be fetched from this
network (residual R-1) and no restriction is circumvented.

Usage: python scripts/fetch_binance_klines.py --symbols BTC-USDT,ETH-USDT,SOL-USDT \
          --timeframe 5m --start 2023-11-01 --end 2026-11-01 --out data/import
Only CLOSED candles are written; the request range is recorded in the sidecar.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from asta.core.timeutil import from_epoch_ms
from asta.exchanges.binance.adapter import REST_BASE, BinancePublicAdapter
from asta.exchanges.common.base import BINANCE_ALLOWLIST
from asta.exchanges.common.http import PublicHttp, RateLimited
from asta.research.data.profiles import RuleProfile

INTERVAL_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000}


def to_ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)


async def fetch_symbol(http: PublicHttp, symbol: str, tf: str, start: int, end: int) -> list[list[Any]]:
    native = BINANCE_ALLOWLIST.native(symbol)
    rows: list[list[Any]] = []
    cursor = start
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    while cursor < end:
        batch = await http.get(
            "/api/v3/klines",
            {"symbol": native, "interval": tf, "startTime": cursor, "endTime": end - 1, "limit": 1000},
        )
        if not batch:
            break
        for r in batch:
            if int(r[6]) < now_ms:  # closed candles only
                rows.append(r)
        cursor = int(batch[-1][0]) + INTERVAL_MS[tf]
        await asyncio.sleep(0.12)  # polite pacing; weight 2 per request
    return rows


def write_files(out: Path, symbol: str, tf: str, rows: list[list[Any]], start: int, end: int) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    name = f"binance_{symbol}_{tf}_{datetime.fromtimestamp(start / 1000, UTC):%Y%m%d}_{datetime.fromtimestamp(end / 1000, UTC):%Y%m%d}.csv"
    path = out / name
    lines = ["open_time,open,high,low,close,volume"]
    for r in rows:
        lines.append(f"{int(r[0])},{r[1]},{r[2]},{r[3]},{r[4]},{r[5]}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    meta = {
        "venue": "BINANCE",
        "symbol": symbol,
        "timeframe": tf,
        "source": f"{REST_BASE}/api/v3/klines (public market data)",
        "source_type": "EXCHANGE_HISTORICAL",
        "provenance": {
            "tool": "scripts/fetch_binance_klines.py",
            "retrieved_at": datetime.now(UTC).isoformat(),
            "request_start": from_epoch_ms(start).isoformat(),
            "request_end_exclusive": from_epoch_ms(end).isoformat(),
            "rows_received": len(rows),
            "closed_candles_only": True,
            "unverified_note": "values as served by Binance public API on retrieval date; not independently audited",
        },
    }
    path.with_name(path.name + ".meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return path


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BTC-USDT,ETH-USDT,SOL-USDT")
    ap.add_argument("--timeframe", default="5m", choices=sorted(INTERVAL_MS))
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", default="data/import")
    ap.add_argument("--rules", action="store_true", help="also snapshot current symbol rules")
    args = ap.parse_args()
    http = PublicHttp(REST_BASE)
    out = Path(args.out)
    try:
        for symbol in args.symbols.split(","):
            rows = await fetch_symbol(http, symbol, args.timeframe, to_ms(args.start), to_ms(args.end))
            p = write_files(out, symbol, args.timeframe, rows, to_ms(args.start), to_ms(args.end))
            print(f"{symbol}: {len(rows)} rows -> {p}", flush=True)
        if args.rules:
            adapter = BinancePublicAdapter(BINANCE_ALLOWLIST, http)
            rules = await adapter.symbol_rules(args.symbols.split(","))
            profiles = [
                RuleProfile.from_symbol_rules(
                    r,
                    1,
                    verified=False,
                    note="CURRENT rules fetched from public exchangeInfo; applying them to history is an assumption",
                ).to_json()
                for r in rules
            ]
            rp = out / f"rules_binance_{datetime.now(UTC):%Y%m%d}.json"
            rp.write_text(json.dumps(profiles, indent=1), encoding="utf-8")
            print(f"rules -> {rp}")
    except RateLimited as exc:
        print(f"STOPPED (rate limited, not retrying): {exc}", file=sys.stderr)
        return 2
    finally:
        await http.aclose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
