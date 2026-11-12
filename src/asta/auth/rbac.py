"""Static role -> permission map. Anything not listed is denied."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    REVIEWER = "reviewer"
    ADMIN = "admin"


class Permission(StrEnum):
    VIEW_STATUS = "view_status"  # overview, venues, markets, risk
    VIEW_AUDIT = "view_audit"
    CONTROL_HALT = "control_halt"
    MANAGE_USERS = "manage_users"


_MAP: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: frozenset({Permission.VIEW_STATUS}),
    Role.OPERATOR: frozenset({Permission.VIEW_STATUS, Permission.CONTROL_HALT}),
    Role.REVIEWER: frozenset({Permission.VIEW_STATUS, Permission.VIEW_AUDIT}),
    Role.ADMIN: frozenset(Permission),
}

MFA_REQUIRED_ROLES = frozenset({Role.OPERATOR, Role.REVIEWER, Role.ADMIN})


def has_permission(role: str, perm: Permission) -> bool:
    try:
        return perm in _MAP[Role(role)]
    except ValueError:
        return False


def mfa_required(role: str) -> bool:
    try:
        return Role(role) in MFA_REQUIRED_ROLES
    except ValueError:
        return True  # unknown role: strictest
