"""M3-B review R-01: a direct UPDATE can no longer release the kill switch by merely setting a GUC

Revision ID: 0012
Revises: 0011

The 0008 guard accepted any UPDATE active=true->false when the session setting ``asta.kill_release_ok`` was
``on``; any role holding UPDATE (including the table owner) could set it itself. The guard now also requires
that ``session_user`` is a listed releaser AND that a RELEASE event by that same ``session_user`` was written in
the SAME transaction (release_kill_switch() does both). A bare ``SET asta.kill_release_ok='on'; UPDATE ...`` is
refused for every role that is not a listed releaser, and cannot leave an unlogged release for those that are.
This is defence in depth: removing UPDATE/INSERT/DELETE on these tables from every role but the definer owner
(infra/security-host/mac/app-vm/pg_roles_m3b.sql) remains the primary control. Schema/functions only.
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

_NEW = """
CREATE OR REPLACE FUNCTION kill_switch_state_guard() RETURNS trigger AS $$
BEGIN
  IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
    RAISE EXCEPTION 'kill_switch_state is never deleted';
  END IF;
  IF OLD.active AND NOT NEW.active THEN
    IF coalesce(current_setting('asta.kill_release_ok', true), '') <> 'on' THEN
      RAISE EXCEPTION 'kill switch can only be released through release_kill_switch()';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM kill_switch_releasers r WHERE r.name = session_user::text) THEN
      RAISE EXCEPTION 'kill switch release refused: % is not an authorised releaser', session_user;
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM kill_switch_events e
      WHERE e.action = 'RELEASE' AND e.actor = session_user::text AND e.at = now()
    ) THEN
      RAISE EXCEPTION 'kill switch release refused: no RELEASE event was logged in this transaction';
    END IF;
  END IF;
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

_OLD = """
CREATE OR REPLACE FUNCTION kill_switch_state_guard() RETURNS trigger AS $$
BEGIN
  IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
    RAISE EXCEPTION 'kill_switch_state is never deleted';
  END IF;
  IF OLD.active AND NOT NEW.active
     AND coalesce(current_setting('asta.kill_release_ok', true), '') <> 'on' THEN
    RAISE EXCEPTION 'kill switch can only be released through release_kill_switch()';
  END IF;
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""


def upgrade() -> None:
    op.execute(_NEW)


def downgrade() -> None:
    op.execute(_OLD)
