"""Opt-in suppression of repeated, identical headroom rejections (docs/m3a/PAPER-REJECTION-ANALYSIS.md, D1+D2).

Problem: a strategy re-emits the same entry intent on every bar while the trend persists; when the
entry is refused for lack of exposure headroom or cash nothing changes, so ~95 % of the paper
rejections were repeats of an already-refused decision.

Rule: remember the last refusal per intent key (symbol, side, order type, tag). While that refusal
was a *headroom* reason, the discrete-state fingerprint is UNCHANGED (no fill, no order opened or
closed, same cash and held quantity, no such event in other accounts) and less than ``rearm_after``
has passed, the same intent is dropped before it becomes an order. A change of the fingerprint or the
passing of ``rearm_after`` (12 bars, set by the engine; market prices alone can open headroom)
re-arms the intent, so a legitimate entry is delayed by at most ``rearm_after``.

Safety properties: halt reasons (drawdown / daily loss), data-quality blocks, sizing and rule
rejections are NEVER suppressed; the guard only ever REMOVES orders that the risk engine would have
refused again, so it cannot increase exposure. Pure and deterministic (the clock is the data's own
timestamps; no I/O). OFF unless explicitly enabled: when off the engine is bit-for-bit identical to
the accepted M1/M2 build.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

#: rejection reasons that can only clear when exposure or cash changes
HEADROOM_REASONS = frozenset(
    {"RISK_ASSET_EXPOSURE_CAP", "RISK_TOTAL_EXPOSURE_CAP", "INSUFFICIENT_CASH"}
)

IntentKey = tuple[str, str, str, str]  # (symbol, side, order type, tag)
Fingerprint = tuple[Decimal, ...]


@dataclass
class RepeatRejectionGuard:
    rearm_after: timedelta = timedelta(hours=1)
    _last: dict[IntentKey, tuple[str, Fingerprint, datetime]] = field(default_factory=dict)
    suppressed: int = 0

    def should_suppress(self, key: IntentKey, fingerprint: Fingerprint, now: datetime) -> bool:
        """True when this intent was refused for a headroom reason and nothing relevant changed."""
        prev = self._last.get(key)
        if (
            prev is not None
            and prev[0] in HEADROOM_REASONS
            and prev[1] == fingerprint
            and now - prev[2] < self.rearm_after
        ):
            self.suppressed += 1
            return True
        return False

    def record_rejection(
        self, key: IntentKey, reason: str, fingerprint: Fingerprint, now: datetime
    ) -> None:
        if reason in HEADROOM_REASONS:
            self._last[key] = (reason, fingerprint, now)
        else:
            self._last.pop(key, None)

    def record_accepted(self, key: IntentKey) -> None:
        self._last.pop(key, None)
