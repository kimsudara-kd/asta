"""Chronological splits and walk-forward windows. Training always ends before testing starts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


class SplitError(Exception):
    pass


@dataclass(frozen=True)
class Window:
    index: int
    train_start: datetime
    train_end: datetime  # exclusive
    test_start: datetime
    test_end: datetime  # exclusive

    def __post_init__(self) -> None:
        if not (self.train_start < self.train_end <= self.test_start < self.test_end):
            raise SplitError("window must satisfy train_start < train_end <= test_start < test_end")


def chronological_split(
    start: datetime, end: datetime, train_fraction: float
) -> tuple[tuple[datetime, datetime], tuple[datetime, datetime]]:
    """One in-sample / out-of-sample split. The boundary is a whole day."""
    if not 0 < train_fraction < 1:
        raise SplitError("train_fraction must be in (0, 1)")
    total_days = (end - start).days
    cut = start + timedelta(days=int(total_days * train_fraction))
    if not start < cut < end:
        raise SplitError("period too short to split")
    return (start, cut), (cut, end)


def rolling_windows(
    start: datetime,
    end: datetime,
    train_days: int,
    test_days: int,
    step_days: int | None = None,
    embargo_days: int = 0,
) -> list[Window]:
    """Rolling train/test windows. `embargo_days` leaves a purge gap between train and test."""
    step = step_days or test_days
    out: list[Window] = []
    t0 = start
    i = 0
    while True:
        train_end = t0 + timedelta(days=train_days)
        test_start = train_end + timedelta(days=embargo_days)
        test_end = test_start + timedelta(days=test_days)
        if test_end > end:
            break
        out.append(Window(i, t0, train_end, test_start, test_end))
        t0 += timedelta(days=step)
        i += 1
    if not out:
        raise SplitError("period too short for even one window")
    return out


def anchored_windows(
    start: datetime, end: datetime, initial_train_days: int, test_days: int, embargo_days: int = 0
) -> list[Window]:
    """Expanding (anchored) training window: always from `start`."""
    out: list[Window] = []
    train_end = start + timedelta(days=initial_train_days)
    i = 0
    while True:
        test_start = train_end + timedelta(days=embargo_days)
        test_end = test_start + timedelta(days=test_days)
        if test_end > end:
            break
        out.append(Window(i, start, train_end, test_start, test_end))
        train_end += timedelta(days=test_days)
        i += 1
    if not out:
        raise SplitError("period too short for even one window")
    return out
