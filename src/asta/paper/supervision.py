"""Operational-availability arithmetic for the supervised observation (M2-R1). Pure functions.

Honest accounting rules:
* a time slice counts as AVAILABLE only if a health sample says the service process was alive, the
  database reachable and the worker heartbeat fresh;
* time with NO samples (the supervisor itself was not running) is UNMONITORED and never counts as
  uptime;
* feed freshness is measured in real time (events arriving now), which is NOT the same as eventual
  candle completeness after a backfill;
* recovery time = from the first unavailable sample to the first available sample after it.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Sample:
    ts: datetime
    child_alive: bool
    db_ok: bool
    worker_ok: bool
    feeds: dict[str, bool] = field(default_factory=dict)
    disabled: tuple[str, ...] = ()  # venues the service itself reports as suspended (reason stored)

    @property
    def available(self) -> bool:
        return self.child_alive and self.db_ok and self.worker_ok

    def to_json(self) -> dict[str, Any]:
        return {
            "ts": self.ts.isoformat(),
            "child_alive": self.child_alive,
            "db_ok": self.db_ok,
            "worker_ok": self.worker_ok,
            "feeds": self.feeds,
            "disabled": list(self.disabled),
        }

    @staticmethod
    def from_json(d: dict[str, Any]) -> Sample:
        return Sample(
            datetime.fromisoformat(d["ts"]),
            bool(d["child_alive"]),
            bool(d["db_ok"]),
            bool(d["worker_ok"]),
            {k: bool(v) for k, v in d.get("feeds", {}).items()},
            tuple(d.get("disabled", ())),
        )


def load_samples(path: Path) -> list[Sample]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(Sample.from_json(json.loads(line)))
    return sorted(out, key=lambda s: s.ts)


def summarize(
    samples: Sequence[Sample], since: datetime, until: datetime, interval_s: float = 10.0
) -> dict[str, Any]:
    """Availability over [since, until]. Slice i covers [t_i, t_{i+1}); a slice longer than
    3 sampling intervals means the supervisor was not watching and is UNMONITORED."""
    win = [s for s in samples if since <= s.ts <= until]
    total = (until - since).total_seconds()
    avail = unavail = unmon = 0.0
    feed_fresh: dict[str, float] = {}
    feed_seen: dict[str, float] = {}
    episodes: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    feed_out: dict[str, dict[str, Any]] = {}
    feed_intervals: dict[str, list[dict[str, Any]]] = {}
    prev_end = since
    max_gap = 3 * interval_s
    for i, s in enumerate(win):
        nxt = win[i + 1].ts if i + 1 < len(win) else min(until, s.ts)
        dur = (nxt - s.ts).total_seconds()
        lead = (s.ts - prev_end).total_seconds()
        if lead > max_gap:
            unmon += lead
        if dur > max_gap:
            unmon += dur
            dur = 0.0
        if s.available:
            avail += dur
            if cur is not None:
                cur["end"] = s.ts
                cur["recovery_seconds"] = (s.ts - cur["start"]).total_seconds()
                episodes.append(cur)
                cur = None
        else:
            unavail += dur
            if cur is None:
                cause = (
                    "process_exit"
                    if not s.child_alive
                    else "database"
                    if not s.db_ok
                    else "worker_heartbeat"
                )
                cur = {"start": s.ts, "cause": cause}
        for v, ok in s.feeds.items():
            feed_seen[v] = feed_seen.get(v, 0.0) + dur
            if ok:
                feed_fresh[v] = feed_fresh.get(v, 0.0) + dur
                if v in feed_out:  # outage over
                    o = feed_out.pop(v)
                    o["end"] = s.ts.isoformat()
                    o["seconds"] = (s.ts - datetime.fromisoformat(o["start"])).total_seconds()
                    feed_intervals.setdefault(v, []).append(o)
            elif v not in feed_out:
                feed_out[v] = {"start": s.ts.isoformat(), "end": None, "seconds": None}
        prev_end = nxt
    if not win:
        unmon = total
    elif (until - prev_end).total_seconds() > max_gap:
        unmon += (until - prev_end).total_seconds()
    if cur is not None:
        cur["end"] = None
        cur["recovery_seconds"] = None
        episodes.append(cur)
    return {
        "window_seconds": total,
        "samples": len(win),
        "available_seconds": avail,
        "unavailable_seconds": unavail,
        "unmonitored_seconds": unmon,
        "service_availability": (avail / total) if total else 0.0,
        "monitored_fraction": 1 - (unmon / total) if total else 0.0,
        "feed_outage_intervals": {
            v: feed_intervals.get(v, []) + ([feed_out[v]] if v in feed_out else [])
            for v in set(feed_seen)
        },
        "feed_fresh_fraction": {
            v: (feed_fresh.get(v, 0.0) / feed_seen[v]) if feed_seen.get(v) else 0.0
            for v in feed_seen
        },
        "downtime_episodes": [
            {
                "start": e["start"].isoformat(),
                "end": e["end"].isoformat() if e["end"] else None,
                "cause": e["cause"],
                "recovery_seconds": e["recovery_seconds"],
            }
            for e in episodes
        ],
    }


def expected_candles(since: datetime, until: datetime) -> int:
    """Closed 1-minute candles per market that exist in [since, until)."""
    return max(0, int((until - since).total_seconds() // 60))


__all__ = ["Sample", "expected_candles", "load_samples", "summarize"]
