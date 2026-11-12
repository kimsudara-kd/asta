"""Out-of-sample bookkeeping: a consumed holdout must not silently influence parameter selection.

Every out-of-sample window evaluated is registered with its validation cycle. Choosing parameters
over data that overlaps a window consumed as a FINAL holdout (in this or any earlier cycle) needs a
NEW cycle, and results of that cycle are flagged `oos_reused` - they are no longer out-of-sample.
Walk-forward test windows consumed earlier in the SAME cycle may feed later training windows (the
standard forward-chaining protocol); that overlap is recorded, not hidden.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

KIND_WF = "WF_TEST"
KIND_FINAL = "FINAL_HOLDOUT"


class HoldoutViolation(Exception):
    pass


@dataclass(frozen=True)
class ConsumedWindow:
    key: str  # strategy id + dataset versions
    start: datetime
    end: datetime
    cycle: int
    kind: str


@dataclass
class HoldoutRegistry:
    consumed: list[ConsumedWindow] = field(default_factory=list)
    cycle: int = 1

    def open_new_cycle(self) -> int:
        self.cycle += 1
        return self.cycle

    def _overlaps(self, key: str, start: datetime, end: datetime) -> list[ConsumedWindow]:
        return [c for c in self.consumed if c.key == key and c.start < end and start < c.end]

    def check_selection(self, key: str, start: datetime, end: datetime) -> bool:
        """Raise if selecting parameters over [start, end) would reuse a consumed holdout without a
        new validation cycle. Returns True when earlier same-cycle WF windows overlap (recorded)."""
        overlap_wf = False
        for c in self._overlaps(key, start, end):
            if c.kind == KIND_FINAL and c.cycle >= self.cycle:
                raise HoldoutViolation(
                    f"selection window overlaps the FINAL holdout {c.start.date()}..{c.end.date()} "
                    f"consumed in cycle {c.cycle}; open a new validation cycle first"
                )
            if c.kind == KIND_WF and c.cycle < self.cycle:
                overlap_wf = (
                    True  # reuse from an earlier cycle: allowed only in a new cycle (we are in one)
                )
            if c.kind == KIND_WF and c.cycle == self.cycle:
                overlap_wf = True
        return overlap_wf

    def reuse_in_new_cycle(self, key: str, start: datetime, end: datetime) -> bool:
        """True when this cycle's selection window overlaps anything consumed in an earlier cycle."""
        return any(c.cycle < self.cycle for c in self._overlaps(key, start, end))

    def consume(self, key: str, start: datetime, end: datetime, kind: str) -> None:
        self.consumed.append(ConsumedWindow(key, start, end, self.cycle, kind))

    def to_json(self) -> list[dict[str, Any]]:
        return [
            {
                "key": c.key,
                "start": c.start.isoformat(),
                "end": c.end.isoformat(),
                "cycle": c.cycle,
                "kind": c.kind,
            }
            for c in self.consumed
        ]
