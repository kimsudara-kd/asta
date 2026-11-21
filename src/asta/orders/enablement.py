"""Runtime enablement of testnet execution: a read-only, fail-closed view of the database state.

Turning it ON is possible only through ``enable_testnet_execution(hours, reason)`` (migration 0013)
by a listed releaser; nothing in this module, or anywhere in ``src/``, can do that. This class only
ASKS. Any error, timeout or missing function answers False.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from asta.risk.gateway import ExecutionGateway


class SqlEnablement:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def active(self, scope: str) -> bool:
        try:
            with self._sessions() as db:
                return bool(
                    db.execute(text("SELECT execution_enabled(:s)"), {"s": scope}).scalar_one()
                )
        except Exception:  # noqa: BLE001 - fail closed on ANY problem
            return False


def testnet_gateway(sessions: sessionmaker[Session]) -> ExecutionGateway:
    """The gateway a testnet process uses: DENY unless the constant AND the enablement hold."""
    return ExecutionGateway(SqlEnablement(sessions))
