"""Versioned exchange-rule and fee profiles.

Nothing here is claimed to be VERIFIED historical truth unless `verified=True` AND `source` names
evidence. Current exchange rules applied to past data are an ASSUMPTION; sensitivity analysis
covers fee uncertainty.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from asta.core.events import SymbolRules
from asta.core.money import to_decimal


class FeeAssetPolicy(StrEnum):
    RECEIVED_ASSET = "RECEIVED_ASSET"  # buy: fee in base; sell: fee in quote
    QUOTE = "QUOTE"  # fee always in quote


@dataclass(frozen=True)
class RuleProfile:
    profile_id: str  # e.g. "BINANCE:BTC-USDT:2026-11-12:v1"
    venue: str
    symbol: str
    tick_size: Decimal
    step_size: Decimal
    min_qty: Decimal
    min_notional: Decimal
    source: str
    retrieved_at: datetime
    verified: bool
    note: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "venue": self.venue,
            "symbol": self.symbol,
            "tick_size": str(self.tick_size),
            "step_size": str(self.step_size),
            "min_qty": str(self.min_qty),
            "min_notional": str(self.min_notional),
            "source": self.source,
            "retrieved_at": self.retrieved_at.isoformat(),
            "verified": self.verified,
            "note": self.note,
        }

    @staticmethod
    def from_json(d: dict[str, Any]) -> RuleProfile:
        return RuleProfile(
            profile_id=str(d["profile_id"]),
            venue=str(d["venue"]),
            symbol=str(d["symbol"]),
            tick_size=to_decimal(str(d["tick_size"])),
            step_size=to_decimal(str(d["step_size"])),
            min_qty=to_decimal(str(d["min_qty"])),
            min_notional=to_decimal(str(d["min_notional"])),
            source=str(d["source"]),
            retrieved_at=datetime.fromisoformat(str(d["retrieved_at"])),
            verified=bool(d["verified"]),
            note=str(d.get("note", "")),
        )

    @staticmethod
    def from_symbol_rules(
        r: SymbolRules, version: int, verified: bool, note: str = ""
    ) -> RuleProfile:
        """Convert an M0 `SymbolRules` (reuse, no parallel model) into a versioned profile."""
        return RuleProfile(
            profile_id=f"{r.venue.value}:{r.symbol}:{r.retrieved_at.date().isoformat()}:v{version}",
            venue=r.venue.value,
            symbol=r.symbol,
            tick_size=r.tick_size,
            step_size=r.step_size,
            min_qty=r.min_qty,
            min_notional=r.min_notional if r.min_notional is not None else Decimal(0),
            source=f"{r.source}: exchange rules snapshot",
            retrieved_at=r.retrieved_at,
            verified=verified,
            note=note,
        )


@dataclass(frozen=True)
class FeeSchedule:
    schedule_id: str
    venue: str
    maker_bps: Decimal
    taker_bps: Decimal
    fee_asset: FeeAssetPolicy
    source: str
    verified: bool
    note: str = ""

    def scaled(self, multiplier: Decimal, suffix: str) -> FeeSchedule:
        """Fee-sensitivity helper (0 = zero-fee scenario)."""
        return FeeSchedule(
            f"{self.schedule_id}*{suffix}",
            self.venue,
            self.maker_bps * multiplier,
            self.taker_bps * multiplier,
            self.fee_asset,
            self.source,
            False,
            f"scaled x{multiplier} from {self.schedule_id}",
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "schedule_id": self.schedule_id,
            "venue": self.venue,
            "maker_bps": str(self.maker_bps),
            "taker_bps": str(self.taker_bps),
            "fee_asset": self.fee_asset.value,
            "source": self.source,
            "verified": self.verified,
            "note": self.note,
        }

    @staticmethod
    def from_json(d: dict[str, Any]) -> FeeSchedule:
        return FeeSchedule(
            str(d["schedule_id"]),
            str(d["venue"]),
            to_decimal(str(d["maker_bps"])),
            to_decimal(str(d["taker_bps"])),
            FeeAssetPolicy(d["fee_asset"]),
            str(d["source"]),
            bool(d["verified"]),
            str(d.get("note", "")),
        )


# Default fee assumptions: standard published base-tier rates as recalled; NOT verified for any
# account and NOT retrieved from the exchanges in M1 (OKX is unreachable, R-1). Fee sensitivity
# (0x, 1x, 2x, 3x) is part of every research report.
BINANCE_DEFAULT_FEES = FeeSchedule(
    "BINANCE-SPOT-BASE-ASSUMED-v1",
    "BINANCE",
    Decimal("10"),
    Decimal("10"),
    FeeAssetPolicy.RECEIVED_ASSET,
    "ASSUMPTION: standard base-tier 0.10 %/0.10 %; unverified",
    False,
    "not account-specific; BNB discount not modelled",
)
OKX_DEFAULT_FEES = FeeSchedule(
    "OKX-SPOT-BASE-ASSUMED-v1",
    "OKX",
    Decimal("8"),
    Decimal("10"),
    FeeAssetPolicy.RECEIVED_ASSET,
    "ASSUMPTION: standard base-tier 0.08 %/0.10 %; unverified (OKX unreachable, residual R-1)",
    False,
    "not account-specific",
)

DEFAULT_FEES: dict[str, FeeSchedule] = {"BINANCE": BINANCE_DEFAULT_FEES, "OKX": OKX_DEFAULT_FEES}


def fixture_rules(symbols: Iterable[str], retrieved_at: datetime) -> tuple[RuleProfile, ...]:
    """Generic FIXTURE rules for synthetic/test data. Never presented as exchange rules."""
    return tuple(
        RuleProfile(
            f"FIXTURE:{s}:v1",
            "BINANCE",
            s,
            Decimal("0.01"),
            Decimal("0.001"),
            Decimal("0.001"),
            Decimal("5"),
            "FIXTURE: generic test rules (not exchange rules)",
            retrieved_at,
            False,
            "synthetic/fixture data only",
        )
        for s in symbols
    )


def okx_assumed_from(binance_rules: Iterable[RuleProfile]) -> tuple[RuleProfile, ...]:
    """OKX rules are unreachable from this network (residual R-1). Reuse the Binance numbers as an
    explicit, UNVERIFIED assumption so OKX-shaped runs are possible but never claimed verified."""
    out = []
    for r in binance_rules:
        out.append(
            RuleProfile(
                r.profile_id.replace("BINANCE", "OKX-ASSUMED", 1),
                "OKX",
                r.symbol,
                r.tick_size,
                r.step_size,
                r.min_qty,
                r.min_notional,
                "ASSUMPTION: Binance values reused; OKX instrument data unreachable (residual R-1)",
                r.retrieved_at,
                False,
                "UNVERIFIED",
            )
        )
    return tuple(out)
