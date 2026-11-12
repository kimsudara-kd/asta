"""Authentication service: Argon2id, TOTP MFA, opaque DB sessions, lockout, audit."""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session

from asta.auth.rbac import mfa_required
from asta.core.timeutil import utcnow
from asta.db.audit_store import append_audit
from asta.db.models import Session as SessionRow
from asta.db.models import User

MAX_FAILED = 5
LOCK_FOR = timedelta(minutes=15)
MIN_PASSWORD_LEN = 12


def _build_hasher() -> PasswordHasher:
    """Argon2id. Defaults follow OWASP (19 MiB, t=2, p=1). Lower memory is allowed only with
    higher time cost (OWASP alternative: 7 MiB, t=5); anything weaker is refused."""
    mem = int(os.environ.get("ASTA_ARGON2_MEMORY_KIB", "19456"))
    t = int(os.environ.get("ASTA_ARGON2_TIME_COST", "2"))
    if mem < 7168 or (mem < 19456 and t < 5) or t < 2:
        raise RuntimeError("Argon2 parameters below the OWASP minimum are not permitted")
    return PasswordHasher(time_cost=t, memory_cost=mem, parallelism=1)


_ph = _build_hasher()


class AuthError(Exception):
    """Deliberately generic: never reveal whether the user exists."""


def _fernet() -> Fernet:
    key = os.environ.get("ASTA_SECRET_KEY")
    if not key:
        raise AuthError("ASTA_SECRET_KEY not configured")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_user(
    db: Session, username: str, password: str, role: str, actor: str = "system"
) -> User:
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError("password too short")
    user = User(
        username=username,
        password_hash=_ph.hash(password),
        role=role,
        mfa_enrolled=False,
        failed_logins=0,
    )
    db.add(user)
    db.flush()
    append_audit(db, actor, "user.create", {"user": username, "role": role})
    return user


def enroll_mfa(db: Session, user: User) -> str:
    """Generate a TOTP secret (encrypted at rest). Returns the provisioning URI (shown once)."""
    secret = pyotp.random_base32()
    user.totp_secret_enc = _fernet().encrypt(secret.encode()).decode()
    user.mfa_enrolled = True
    append_audit(db, user.username, "mfa.enroll", {})
    return pyotp.TOTP(secret).provisioning_uri(name=user.username, issuer_name="ASTA")


def _totp(user: User) -> pyotp.TOTP:
    assert user.totp_secret_enc
    return pyotp.TOTP(_fernet().decrypt(user.totp_secret_enc.encode()).decode())


@dataclass(frozen=True)
class LoginResult:
    token: str
    csrf_token: str
    needs_mfa: bool


def _fail(db: Session, user: User | None, username: str, why: str, now: datetime) -> AuthError:
    if user is not None:
        user.failed_logins += 1
        if user.failed_logins >= MAX_FAILED:
            user.locked_until = now + LOCK_FOR
            append_audit(db, username, "auth.lockout", {})
    append_audit(db, username[:64], "auth.login_failed", {"reason": why})
    db.commit()
    return AuthError("invalid credentials")


def login(db: Session, username: str, password: str, ttl: timedelta) -> LoginResult:
    now = utcnow()
    user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if user is not None and user.locked_until and user.locked_until > now:
        append_audit(db, username[:64], "auth.login_failed", {"reason": "locked"})
        db.commit()
        raise AuthError("invalid credentials")
    try:
        if user is None:
            _ph.verify(_DUMMY_HASH, password)  # equalise timing
            raise VerifyMismatchError
        _ph.verify(user.password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        raise _fail(db, user, username, "bad_password", now) from None
    assert user is not None
    user.failed_logins = 0
    needs_mfa = mfa_required(user.role)
    if needs_mfa and not user.mfa_enrolled:
        # role requires MFA but none enrolled: refuse to issue any session (fail-closed)
        raise _fail(db, user, username, "mfa_not_enrolled", now)
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    db.add(
        SessionRow(
            user_id=user.id,
            token_hash=hash_token(token),
            csrf_token=csrf,
            mfa_passed=not needs_mfa,
            expires_at=now + ttl,
            revoked=False,
        )
    )
    append_audit(db, username, "auth.password_ok", {"mfa_pending": needs_mfa})
    db.commit()
    return LoginResult(token, csrf, needs_mfa)


def verify_mfa(db: Session, token: str, code: str) -> None:
    sess = _load_session(db, token, require_mfa=False)
    if sess is None:
        raise AuthError("invalid session")
    user = db.get(User, sess.user_id)
    assert user is not None
    if not user.totp_secret_enc or not _totp(user).verify(code, valid_window=1):
        user.failed_logins += 1
        if user.failed_logins >= MAX_FAILED:
            user.locked_until = utcnow() + LOCK_FOR
            sess.revoked = True
            append_audit(db, user.username, "auth.lockout", {})
        append_audit(db, user.username, "auth.mfa_failed", {})
        db.commit()
        raise AuthError("invalid code")
    user.failed_logins = 0
    sess.mfa_passed = True
    append_audit(db, user.username, "auth.mfa_ok", {})
    db.commit()


def _load_session(db: Session, token: str, require_mfa: bool) -> SessionRow | None:
    row = db.execute(
        select(SessionRow).where(SessionRow.token_hash == hash_token(token))
    ).scalar_one_or_none()
    if row is None or row.revoked or row.expires_at <= utcnow():
        return None
    if require_mfa and not row.mfa_passed:
        return None
    return row


@dataclass(frozen=True)
class Principal:
    user_id: str
    username: str
    role: str
    csrf_token: str


def authenticate(db: Session, token: str | None) -> Principal | None:
    if not token:
        return None
    sess = _load_session(db, token, require_mfa=True)
    if sess is None:
        return None
    user = db.get(User, sess.user_id)
    if user is None:
        return None
    return Principal(str(user.id), user.username, user.role, sess.csrf_token)


def logout(db: Session, token: str) -> None:
    sess = _load_session(db, token, require_mfa=False)
    if sess is not None:
        sess.revoked = True
        db.commit()


def csrf_ok(expected: str, supplied: str | None) -> bool:
    return bool(supplied) and secrets.compare_digest(expected, supplied or "")


_DUMMY_HASH = _ph.hash("dummy-password-for-timing")  # for constant-ish timing on unknown users
