"""Seed a DISPOSABLE e2e database: migrate, create 3 test users, write their credentials.

Usage: ASTA_DATABASE_URL=... ASTA_SECRET_KEY=... python scripts/e2e_seed.py <secrets.json>
The secrets file holds throw-away test credentials for a disposable database; it is git-ignored
and deleted by the e2e runner. Refuses to run unless the database name contains "e2e".
"""

from __future__ import annotations

import json
import secrets
import subprocess
import sys

import pyotp

from asta.auth import service as auth
from asta.core.settings import load_settings
from asta.db.session import make_engine, make_session_factory

NL = chr(10)


def write_import_files(import_dir: str, n_bars: int = 20000) -> None:
    """Deterministic SYNTHETIC 1h datasets (labelled as such) for the research browser tests."""
    import os
    from datetime import UTC, datetime
    from decimal import Decimal

    from asta.research.data.fixtures import DOWNTREND, RANGE, UPTREND, generate_bars, seg
    from asta.research.types import Timeframe

    os.makedirs(import_dir, exist_ok=True)
    third = n_bars // 3
    for i, sym in enumerate(("BTC-USDT", "ETH-USDT", "SOL-USDT")):
        bars = generate_bars(
            datetime(2024, 1, 1, tzinfo=UTC),
            Timeframe.H1,
            [seg(RANGE, third), seg(UPTREND, third), seg(DOWNTREND, n_bars - 2 * third)],
            100 + i,
            Decimal("100"),
        )
        name = f"synthetic_{sym}_1h.csv"
        lines = ["open_time,open,high,low,close,volume"] + [
            f"{int(b.open_time.timestamp() * 1000)},{b.open},{b.high},{b.low},{b.close},{b.volume}"
            for b in bars
        ]
        with open(os.path.join(import_dir, name), "w", encoding="utf-8") as f:
            f.write(NL.join(lines) + NL)
        with open(os.path.join(import_dir, name + ".meta.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "venue": "BINANCE",
                    "symbol": sym,
                    "timeframe": "1h",
                    "source": "e2e synthetic generator",
                    "source_type": "SYNTHETIC",
                },
                f,
            )


def main(out: str, import_dir: str | None = None) -> None:
    settings = load_settings()
    if "e2e" not in settings.database_url.rsplit("/", 1)[-1]:
        raise SystemExit("refusing: database name must contain 'e2e'")
    subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
    )
    creds: dict[str, dict[str, str]] = {}
    with make_session_factory(make_engine(settings.database_url))() as db:
        for name, role in (("viewer1", "viewer"), ("op1", "operator"), ("rev1", "reviewer")):
            pw = secrets.token_urlsafe(18)
            user = auth.create_user(db, name, pw, role, actor="e2e-seed")
            entry = {"password": pw, "role": role}
            if role != "viewer":
                entry["totp_secret"] = pyotp.parse_uri(auth.enroll_mfa(db, user)).secret
            creds[name] = entry
        db.commit()
    with open(out, "w", encoding="utf-8") as f:
        json.dump(creds, f)
    if import_dir:
        write_import_files(import_dir)
    print(f"seeded {', '.join(creds)}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
