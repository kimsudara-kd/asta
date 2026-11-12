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


def main(out: str) -> None:
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
    print(f"seeded {', '.join(creds)}")


if __name__ == "__main__":
    main(sys.argv[1])
