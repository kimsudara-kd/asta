"""M3-B-R4: the kill-switch release identity comes from the authenticated session, not a parameter (G-10)

Revision ID: 0011
Revises: 0010

release_kill_switch(p_actor, p_reason) accepted the actor NAME as an argument, so any role allowed to
execute it could claim another listed releaser's name. It is replaced by release_kill_switch(p_reason):
the actor is ``session_user`` (the login role that authenticated to the database, unchanged inside a
SECURITY DEFINER function), which must be listed in kill_switch_releasers (empty by default). Manual
authorisation, the >= 10 character reason and the append-only event log are unchanged.
Schema/functions only; nothing here sends or receives an order.
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS release_kill_switch(text, text)")
    op.execute(
        """
        CREATE FUNCTION release_kill_switch(p_reason text) RETURNS boolean AS $$
        DECLARE is_on boolean; who text := session_user::text;
        BEGIN
          PERFORM 1 FROM kill_switch_state WHERE id = 1 FOR UPDATE;
          SELECT active INTO is_on FROM kill_switch_state WHERE id = 1;
          IF NOT is_on THEN RETURN false; END IF;
          IF NOT EXISTS (SELECT 1 FROM kill_switch_releasers r WHERE r.name = who) THEN
            RAISE EXCEPTION 'release refused: % is not an authorised releaser', who;
          END IF;
          IF p_reason IS NULL OR length(trim(p_reason)) < 10 THEN
            RAISE EXCEPTION 'release refused: an auditable reason of at least 10 characters is required';
          END IF;
          INSERT INTO kill_switch_events (action, cause, actor, reason) VALUES ('RELEASE', NULL, who, p_reason);
          PERFORM set_config('asta.kill_release_ok', 'on', true);
          UPDATE kill_switch_state SET active = false WHERE id = 1;
          PERFORM set_config('asta.kill_release_ok', 'off', true);
          RETURN true;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
        """
    )
    op.execute("REVOKE ALL ON FUNCTION release_kill_switch(text) FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS release_kill_switch(text)")
    op.execute(
        """
        CREATE FUNCTION release_kill_switch(p_actor text, p_reason text) RETURNS boolean AS $$
        DECLARE is_on boolean;
        BEGIN
          PERFORM 1 FROM kill_switch_state WHERE id = 1 FOR UPDATE;
          SELECT active INTO is_on FROM kill_switch_state WHERE id = 1;
          IF NOT is_on THEN RETURN false; END IF;
          IF p_actor IS NULL OR NOT EXISTS (SELECT 1 FROM kill_switch_releasers r WHERE r.name = p_actor) THEN
            RAISE EXCEPTION 'release refused: % is not an authorised releaser', p_actor;
          END IF;
          IF p_reason IS NULL OR length(trim(p_reason)) < 10 THEN
            RAISE EXCEPTION 'release refused: an auditable reason of at least 10 characters is required';
          END IF;
          INSERT INTO kill_switch_events (action, cause, actor, reason) VALUES ('RELEASE', NULL, p_actor, p_reason);
          PERFORM set_config('asta.kill_release_ok', 'on', true);
          UPDATE kill_switch_state SET active = false WHERE id = 1;
          PERFORM set_config('asta.kill_release_ok', 'off', true);
          RETURN true;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
        """
    )
    op.execute("REVOKE ALL ON FUNCTION release_kill_switch(text, text) FROM PUBLIC")
