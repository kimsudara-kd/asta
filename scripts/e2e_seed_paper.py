"""Seed a DISPOSABLE e2e database with a paper session built from SYNTHETIC bars (browser tests).

Usage: ASTA_DATABASE_URL=... python scripts/e2e_seed_paper.py
Refuses to run unless the database name contains "e2e". Everything it writes is test fixture data:
the session's step inputs are synthetic hourly bars (reports label them 'NO_STORED_OBSERVATIONS
(fixture/test input)'), the feed rows are marked CONNECTED with a future timestamp so the UI's
session-creation test has a 'verified' feed for the duration of the run, and the incident is a fixture.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from sqlalchemy import text

from asta.core.settings import load_settings
from asta.db.models import MarketRulesVersion
from asta.db.paper_models import MarketFeedConnection
from asta.db.session import make_engine, make_session_factory
from asta.paper import ops, store
from asta.paper.collector import set_feed_state
from asta.paper.config import PaperSessionConfig, StrategySpec
from asta.paper.runtime import PaperRuntime
from asta.paper.states import SessionState
from asta.research.data.fixtures import DOWNTREND, HIGHVOL, RANGE, UPTREND, generate_bars, seg
from asta.research.data.profiles import DEFAULT_FEES, RuleProfile
from asta.research.types import Timeframe

SYMS = ("BTC-USDT", "ETH-USDT", "SOL-USDT")
T0 = datetime(2026, 1, 1, tzinfo=UTC)
WARM = 300
N = 460


def main() -> int:
    settings = load_settings()
    if "e2e" not in settings.database_url.rsplit("/", 1)[-1]:
        print("refusing: not an e2e database", file=sys.stderr)
        return 2
    factory = make_session_factory(make_engine(settings.database_url))
    now = datetime.now(UTC)
    with factory() as db:
        for v in ("BINANCE", "OKX"):
            set_feed_state(db, v, "CONNECTED", "LIVE", "e2e fixture", "e2e", now=now)
            db.get(MarketFeedConnection, v).last_event_at = now + timedelta(days=1)
            for sym in SYMS:
                db.add(
                    MarketRulesVersion(
                        venue=v,
                        symbol=sym,
                        tick_size=D("0.01"),
                        step_size=D("0.001"),
                        min_qty=D("0.001"),
                        min_notional=D("5"),
                        trading=True,
                        source="LIVE",
                        retrieved_at=now,
                    )
                )
        db.commit()
        rules = {
            v: tuple(
                RuleProfile(
                    f"E2E:{v}:{s}",
                    v,
                    s,
                    D("0.01"),
                    D("0.001"),
                    D("0.001"),
                    D("5"),
                    "e2e fixture",
                    T0,
                    False,
                )
                for s in SYMS
            )
            for v in ("BINANCE", "OKX")
        }
        cfg = PaperSessionConfig(
            name="e2e fixture session",
            wallets={"BINANCE": D("500"), "OKX": D("500")},
            enabled_venues=("BINANCE", "OKX"),
            strategies=(
                StrategySpec("regime_grid"),
                StrategySpec("trend_pullback"),
                StrategySpec("sma_cross"),
            ),
            rules=rules,
            fees={v: DEFAULT_FEES[v] for v in ("BINANCE", "OKX")},
            timeframe=Timeframe.H1,
        )
        s = store.create_session(db, cfg, "e2e-seed")
        for st in (SessionState.INITIALIZING, SessionState.READY, SessionState.RUNNING):
            store.transition(db, s.id, st, "e2e-seed")
        db.commit()
        segs = [
            seg(RANGE, 200),
            seg(UPTREND, 250),
            seg(RANGE, 150),
            seg(DOWNTREND, 250),
            seg(HIGHVOL, 100),
            seg(UPTREND, 300),
        ]
        per = {
            f"{v}:{sym}": generate_bars(T0, Timeframe.H1, segs, 3 + 10 * vi + si, D("100"))
            for vi, v in enumerate(("BINANCE", "OKX"))
            for si, sym in enumerate(SYMS)
        }
        rt = PaperRuntime(cfg)
        for k in range(N):
            ts = T0 + timedelta(hours=k)
            bars = {key: b[k] for key, b in per.items()}
            rows = rt.step(k, ts, bars, {}, True, k < WARM)
            store.persist_step(db, s.id, k, ts, k < WARM, bars, {}, True, [], rows)
            if k % 40 == 39:
                db.commit()
        db.commit()
        ops.open_incident(
            db,
            ops.DATA_GAP,
            ops.SEV_WARN,
            "e2e fixture incident (not a real market event)",
            f"e2e:{s.id}",
            s.id,
            "BINANCE",
            "BTC-USDT",
        )
        db.execute(text("select 1"))
        db.commit()
        print("seeded paper session", s.id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
