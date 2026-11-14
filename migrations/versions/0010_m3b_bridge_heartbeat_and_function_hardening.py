"""M3-B-R3: bridge heartbeat (fail-closed supervision) and hardened halt functions

Revision ID: 0010
Revises: 0009

* risk_halt_bridge_heartbeat + bridge_heartbeat(): the supervised bridge records that it is alive on
  every successful poll. A gate built with BridgedKillState treats a stale or missing heartbeat as a
  halt, so a dead bridge can never leave the order path open (fail closed).
* trip_kill_switch(), release_kill_switch(), bridge_heartbeat() become SECURITY DEFINER with a pinned
  search_path and EXECUTE is revoked from PUBLIC: a service role gets exactly the functions it needs
  (infra/security-host/mac/app-vm/pg_roles_m3b.sql) and no table-level write on the halt tables.
Schema/functions only; nothing here sends or receives an order.
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

FUNCS = (
    "trip_kill_switch(text, text, text)",
    "release_kill_switch(text, text)",
)


def upgrade() -> None:
    op.create_table(
        "risk_halt_bridge_heartbeat",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("beat_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_rhbh_single_row"),
    )
    op.execute(
        "INSERT INTO risk_halt_bridge_heartbeat (id, beat_at) VALUES (1, 'epoch')"
    )  # stale until the first beat
    op.execute(
        """
        CREATE FUNCTION bridge_heartbeat() RETURNS void AS $$
          UPDATE risk_halt_bridge_heartbeat SET beat_at = now() WHERE id = 1
        $$ LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp
        """
    )
    for f in FUNCS:
        op.execute(f"ALTER FUNCTION {f} SECURITY DEFINER SET search_path = public, pg_temp")
    for f in (*FUNCS, "bridge_heartbeat()"):
        op.execute(f"REVOKE ALL ON FUNCTION {f} FROM PUBLIC")


def downgrade() -> None:
    for f in (*FUNCS, "bridge_heartbeat()"):
        op.execute(f"GRANT EXECUTE ON FUNCTION {f} TO PUBLIC")
    for f in FUNCS:
        op.execute(f"ALTER FUNCTION {f} SECURITY INVOKER RESET search_path")
    op.execute("DROP FUNCTION IF EXISTS bridge_heartbeat()")
    op.drop_table("risk_halt_bridge_heartbeat")
