"""M3-B review S-02: close the enable-versus-latch race and never carry an enablement across a release

Revision ID: 0014
Revises: 0013

1. ``enable_testnet_execution`` read ``kill_switch_state`` without a lock, so a latch committing between that read and the
   UPDATE left a future-dated enablement behind a latched switch (inert while latched, but live again on release). It now takes
   ``FOR SHARE`` on the state row: a concurrent latch (which updates that row) is serialised with it.
2. ``execution_auto_off`` now fires on ANY change of the latch (latch or release) and clears the enablement, so releasing the
   kill switch can never re-enable testnet execution; a person must enable it again.
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enable_testnet_execution(p_hours integer, p_reason text) RETURNS timestamptz AS $$
        DECLARE who text := session_user::text; until timestamptz; latched boolean;
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM kill_switch_releasers r WHERE r.name = who) THEN
            RAISE EXCEPTION 'enablement refused: % is not an authorised releaser', who;
          END IF;
          IF p_hours IS NULL OR p_hours < 1 OR p_hours > 24 THEN
            RAISE EXCEPTION 'enablement refused: hours must be between 1 and 24';
          END IF;
          IF p_reason IS NULL OR length(trim(p_reason)) < 10 THEN
            RAISE EXCEPTION 'enablement refused: an auditable reason of at least 10 characters is required';
          END IF;
          SELECT active INTO latched FROM kill_switch_state WHERE id = 1 FOR SHARE;
          IF latched IS NOT FALSE THEN
            RAISE EXCEPTION 'enablement refused: the kill switch is latched';
          END IF;
          INSERT INTO execution_enablement_events (action, actor, hours, reason) VALUES ('ENABLE', who, p_hours, p_reason);
          until := now() + make_interval(hours => p_hours);
          UPDATE execution_enablement SET enabled_until = until, enabled_by = who, reason = p_reason
            WHERE scope = 'BINANCE_TESTNET';
          RETURN until;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION execution_auto_off() RETURNS trigger AS $$
        BEGIN
          IF NEW.active IS DISTINCT FROM OLD.active THEN
            IF (SELECT enabled_until > now() FROM execution_enablement WHERE scope = 'BINANCE_TESTNET') THEN
              INSERT INTO execution_enablement_events (action, actor, reason)
                VALUES ('AUTO_OFF', 'kill-switch',
                        CASE WHEN NEW.active THEN 'the kill switch latched' ELSE 'the kill switch was released' END);
            END IF;
            UPDATE execution_enablement SET enabled_until = 'epoch', enabled_by = 'kill-switch',
              reason = CASE WHEN NEW.active THEN 'the kill switch latched' ELSE 'the kill switch was released' END
              WHERE scope = 'BINANCE_TESTNET' AND enabled_until > 'epoch';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
        """
    )


def downgrade() -> None:
    # restore the 0013 definitions
    op.execute(
        """
        CREATE OR REPLACE FUNCTION execution_auto_off() RETURNS trigger AS $$
        BEGIN
          IF NEW.active AND NOT OLD.active THEN
            IF (SELECT enabled_until > now() FROM execution_enablement WHERE scope = 'BINANCE_TESTNET') THEN
              INSERT INTO execution_enablement_events (action, actor, reason)
                VALUES ('AUTO_OFF', 'kill-switch', 'the kill switch latched');
            END IF;
            UPDATE execution_enablement SET enabled_until = 'epoch', enabled_by = 'kill-switch',
              reason = 'the kill switch latched' WHERE scope = 'BINANCE_TESTNET' AND enabled_until > 'epoch';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enable_testnet_execution(p_hours integer, p_reason text) RETURNS timestamptz AS $$
        DECLARE who text := session_user::text; until timestamptz;
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM kill_switch_releasers r WHERE r.name = who) THEN
            RAISE EXCEPTION 'enablement refused: % is not an authorised releaser', who;
          END IF;
          IF p_hours IS NULL OR p_hours < 1 OR p_hours > 24 THEN
            RAISE EXCEPTION 'enablement refused: hours must be between 1 and 24';
          END IF;
          IF p_reason IS NULL OR length(trim(p_reason)) < 10 THEN
            RAISE EXCEPTION 'enablement refused: an auditable reason of at least 10 characters is required';
          END IF;
          IF (SELECT active FROM kill_switch_state WHERE id = 1) THEN
            RAISE EXCEPTION 'enablement refused: the kill switch is latched';
          END IF;
          INSERT INTO execution_enablement_events (action, actor, hours, reason) VALUES ('ENABLE', who, p_hours, p_reason);
          until := now() + make_interval(hours => p_hours);
          UPDATE execution_enablement SET enabled_until = until, enabled_by = who, reason = p_reason
            WHERE scope = 'BINANCE_TESTNET';
          RETURN until;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
        """
    )
