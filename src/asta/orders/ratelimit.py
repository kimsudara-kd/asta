"""Request-budget guard (pure, injected clock). Exchange limits are READ at runtime, never assumed.

The testnet differs from production (observed 50 orders / 10 s, 160,000 / day). ``Budget`` is built
from limits the caller fetched from the exchange; a missing limit means "no budget": every request
is refused (fail closed). It never sleeps: it answers ``allow`` or ``retry_after`` and the caller
decides. Safety margin: only ``headroom`` (default 80 %) of a window is ever spent.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from fractions import Fraction


@dataclass(frozen=True)
class Window:
    seconds: int
    limit: int  # requests (or weight) allowed per window


@dataclass(frozen=True)
class Decision:
    allowed: bool
    retry_after: float = 0.0
    reason: str = ""


@dataclass
class Budget:
    windows: tuple[Window, ...]
    headroom: Fraction = Fraction(4, 5)
    _events: dict[int, deque[tuple[float, int]]] = field(default_factory=dict)
    _banned_until: float = 0.0

    def _cap(self, w: Window) -> int:
        return int(w.limit * self.headroom)

    def check(self, now: float, weight: int = 1) -> Decision:
        if not self.windows:
            return Decision(False, 0.0, "NO_LIMITS_KNOWN")
        if weight <= 0:
            return Decision(False, 0.0, "BAD_WEIGHT")
        if now < self._banned_until:
            return Decision(False, self._banned_until - now, "BANNED")
        wait = 0.0
        for w in self.windows:
            q = self._events.setdefault(w.seconds, deque())
            while q and q[0][0] <= now - w.seconds:
                q.popleft()
            used = sum(x for _, x in q)
            if weight > self._cap(w):
                return Decision(False, 0.0, f"WEIGHT_EXCEEDS_WINDOW_{w.seconds}s")
            if used + weight > self._cap(w):
                need = used + weight - self._cap(w)  # oldest events must age out until this fits
                freed = 0
                for ts, x in q:
                    freed += x
                    if freed >= need:
                        wait = max(wait, ts + w.seconds - now)
                        break
        if wait > 0:
            return Decision(False, wait, "WINDOW_FULL")
        return Decision(True)

    def spend(self, now: float, weight: int = 1) -> Decision:
        d = self.check(now, weight)
        if d.allowed:
            for w in self.windows:
                self._events.setdefault(w.seconds, deque()).append((now, weight))
        return d

    def exchange_said_slow_down(self, now: float, retry_after: float) -> None:
        """HTTP 429 / 418: stop everything until the exchange's own deadline (never shorten it)."""
        self._banned_until = max(self._banned_until, now + max(retry_after, 1.0))
