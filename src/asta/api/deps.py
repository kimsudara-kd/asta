"""Shared FastAPI dependencies (DB session, authenticated principal with RBAC, CSRF check)."""

from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from asta.auth import service as auth
from asta.auth.rbac import Permission, has_permission

COOKIE = "asta_session"


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.ctx.sessions() as s:
        yield s


Db = Annotated[Session, Depends(get_db)]


def require(perm: Permission | None) -> Callable[..., auth.Principal]:
    """Authenticated (MFA-complete) principal; `perm` is checked with default-deny RBAC."""

    def dep(db: Db, token: Annotated[str | None, Cookie(alias=COOKIE)] = None) -> auth.Principal:
        principal = auth.authenticate(db, token)
        if principal is None:
            raise HTTPException(401, "not authenticated")
        if perm is not None and not has_permission(principal.role, perm):
            raise HTTPException(403, "forbidden")
        return principal

    return dep


def require_csrf(
    principal: Annotated[auth.Principal, Depends(require(None))],
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> auth.Principal:
    """State-changing routes need the session's CSRF token in `X-CSRF-Token`."""
    if not auth.csrf_ok(principal.csrf_token, x_csrf_token):
        raise HTTPException(403, "csrf")
    return principal


def require_write(perm: Permission) -> Callable[..., auth.Principal]:
    """RBAC permission AND a valid CSRF header (for POST routes)."""

    def dep(
        db: Db,
        token: Annotated[str | None, Cookie(alias=COOKIE)] = None,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> auth.Principal:
        principal = auth.authenticate(db, token)
        if principal is None:
            raise HTTPException(401, "not authenticated")
        if not has_permission(principal.role, perm):
            raise HTTPException(403, "forbidden")
        if not auth.csrf_ok(principal.csrf_token, x_csrf_token):
            raise HTTPException(403, "csrf")
        return principal

    return dep
