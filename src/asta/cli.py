"""Operator bootstrap CLI: create a user and enrol TOTP. Passwords are read via getpass only."""

from __future__ import annotations

import argparse
import getpass

from asta.auth import service as auth
from asta.auth.rbac import mfa_required
from asta.core.settings import load_settings
from asta.db.session import make_engine, make_session_factory


def main() -> None:
    ap = argparse.ArgumentParser(prog="asta-cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    cu = sub.add_parser("create-user")
    cu.add_argument("--username", required=True)
    cu.add_argument("--role", required=True, choices=["viewer", "operator", "reviewer", "admin"])
    args = ap.parse_args()
    settings = load_settings()
    with make_session_factory(make_engine(settings.database_url))() as db:
        pw = getpass.getpass("Password (min 12 chars): ")
        if pw != getpass.getpass("Repeat: "):
            raise SystemExit("passwords differ")
        user = auth.create_user(db, args.username, pw, args.role, actor="cli")
        if mfa_required(user.role):
            print("Add this to your authenticator app (shown once):")
            print(auth.enroll_mfa(db, user))
        db.commit()
        print(f"created {args.username} ({args.role})")


if __name__ == "__main__":
    main()
