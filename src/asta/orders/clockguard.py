"""Clock-skew guard (pure). Authenticated exchange requests carry a timestamp and are refused when
it is too far from the exchange's clock, so a drifting host must stop trading BEFORE it is refused.

``estimate`` turns (local send time, exchange server time, local receive time) into an offset with
an uncertainty equal to half the round trip. The guard fails closed: no recent estimate, a high
round trip, or an offset beyond tolerance all forbid sending. It uses integer milliseconds.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Estimate:
    offset_ms: int  # exchange_time - local_time (positive: exchange is ahead)
    uncertainty_ms: int  # half the round trip
    measured_at_ms: int  # local clock when received


def estimate(sent_ms: int, server_ms: int, received_ms: int) -> Estimate:
    if received_ms < sent_ms:
        raise ValueError("local clock went backwards during the measurement")
    mid = sent_ms + (received_ms - sent_ms) // 2
    return Estimate(server_ms - mid, (received_ms - sent_ms + 1) // 2, received_ms)


@dataclass(frozen=True)
class SkewPolicy:
    recv_window_ms: int = 5_000  # what the exchange tolerates (we use well under it)
    max_abs_offset_ms: int = 1_000  # refuse above this estimated skew
    max_uncertainty_ms: int = 800
    max_estimate_age_ms: int = 60_000


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str = ""
    timestamp_ms: int = 0  # the corrected timestamp to put in the request when ok


def verdict(est: Estimate | None, now_local_ms: int, policy: SkewPolicy = SkewPolicy()) -> Verdict:  # noqa: B008
    if est is None:
        return Verdict(False, "NO_CLOCK_ESTIMATE")
    if now_local_ms < est.measured_at_ms:
        return Verdict(False, "LOCAL_CLOCK_WENT_BACKWARDS")
    if now_local_ms - est.measured_at_ms > policy.max_estimate_age_ms:
        return Verdict(False, "CLOCK_ESTIMATE_STALE")
    if est.uncertainty_ms > policy.max_uncertainty_ms:
        return Verdict(False, "CLOCK_ESTIMATE_TOO_UNCERTAIN")
    if abs(est.offset_ms) + est.uncertainty_ms > policy.max_abs_offset_ms:
        return Verdict(False, "CLOCK_SKEW_TOO_LARGE")
    return Verdict(True, "", now_local_ms + est.offset_ms)
