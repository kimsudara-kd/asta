from datetime import timedelta

import pyotp
import pytest

from asta.auth import service as auth
from asta.auth.rbac import Permission, Role, has_permission, mfa_required
from asta.core.timeutil import utcnow
from asta.db.models import Session as SessionRow
from asta.db.models import User

pytestmark = pytest.mark.db
TTL = timedelta(minutes=30)
PW = "correct-horse-battery"


def make_user(db, name="op1", role="operator", mfa=True):
    u = auth.create_user(db, name, PW, role)
    secret = None
    if mfa:
        uri = auth.enroll_mfa(db, u)
        secret = pyotp.parse_uri(uri).secret
    db.commit()
    return u, secret


def test_rbac_matrix():
    assert has_permission("viewer", Permission.VIEW_STATUS)
    assert not has_permission("viewer", Permission.VIEW_AUDIT)
    assert not has_permission("viewer", Permission.CONTROL_HALT)
    assert has_permission("operator", Permission.CONTROL_HALT)
    assert not has_permission("operator", Permission.VIEW_AUDIT)
    assert has_permission("reviewer", Permission.VIEW_AUDIT)
    assert all(has_permission("admin", p) for p in Permission)
    assert not has_permission("nonsense", Permission.VIEW_STATUS)
    assert not mfa_required("viewer")
    assert all(mfa_required(r.value) for r in (Role.OPERATOR, Role.REVIEWER, Role.ADMIN))
    assert mfa_required("nonsense")


def test_password_stored_as_argon2id_and_short_rejected(db):
    u, _ = make_user(db, mfa=False)
    assert u.password_hash.startswith("$argon2id$")
    with pytest.raises(ValueError):
        auth.create_user(db, "x", "short", "viewer")


def test_totp_secret_encrypted_at_rest(db):
    u, secret = make_user(db)
    assert secret not in (u.totp_secret_enc or "")


def test_login_requires_mfa_before_session_works(db):
    u, secret = make_user(db)
    res = auth.login(db, "op1", PW, TTL)
    assert res.needs_mfa
    assert auth.authenticate(db, res.token) is None  # password alone is not enough
    with pytest.raises(auth.AuthError):
        auth.verify_mfa(db, res.token, "000000")
    auth.verify_mfa(db, res.token, pyotp.TOTP(secret).now())
    p = auth.authenticate(db, res.token)
    assert p and p.username == "op1" and p.role == "operator"


def test_token_stored_only_hashed(db):
    make_user(db)
    res = auth.login(db, "op1", PW, TTL)
    rows = db.query(SessionRow).all()
    assert all(res.token != r.token_hash and len(r.token_hash) == 64 for r in rows)


def test_viewer_needs_no_mfa(db):
    make_user(db, "v1", "viewer", mfa=False)
    res = auth.login(db, "v1", PW, TTL)
    assert not res.needs_mfa and auth.authenticate(db, res.token)


def test_mfa_role_without_enrollment_cannot_login(db):
    make_user(db, "op2", "operator", mfa=False)
    with pytest.raises(auth.AuthError):
        auth.login(db, "op2", PW, TTL)


def test_wrong_password_generic_and_unknown_user_same_error(db):
    make_user(db, "v1", "viewer", mfa=False)
    with pytest.raises(auth.AuthError) as a:
        auth.login(db, "v1", "wrong-password-123", TTL)
    with pytest.raises(auth.AuthError) as b:
        auth.login(db, "ghost", "wrong-password-123", TTL)
    assert str(a.value) == str(b.value) == "invalid credentials"


def test_lockout_after_repeated_failures(db):
    make_user(db, "v1", "viewer", mfa=False)
    for _ in range(auth.MAX_FAILED):
        with pytest.raises(auth.AuthError):
            auth.login(db, "v1", "wrong-password-123", TTL)
    with pytest.raises(auth.AuthError):  # even the right password is refused now
        auth.login(db, "v1", PW, TTL)
    assert db.query(User).one().locked_until > utcnow()


def test_expired_and_revoked_sessions_rejected(db):
    make_user(db, "v1", "viewer", mfa=False)
    res = auth.login(db, "v1", PW, TTL)
    row = db.query(SessionRow).one()
    row.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert auth.authenticate(db, res.token) is None
    row.expires_at = utcnow() + TTL
    db.commit()
    assert auth.authenticate(db, res.token)
    auth.logout(db, res.token)
    assert auth.authenticate(db, res.token) is None


def test_csrf_compare():
    assert auth.csrf_ok("abc", "abc")
    assert not auth.csrf_ok("abc", "abd") and not auth.csrf_ok("abc", None)


def test_auth_events_audited_without_secrets(db):
    from asta.db.audit_store import recent_audit

    u, secret = make_user(db)
    auth.login(db, "op1", PW, TTL)
    blob = str([(e.action, e.payload) for e in recent_audit(db, 50)])
    assert "auth.password_ok" in blob and PW not in blob and secret not in blob
