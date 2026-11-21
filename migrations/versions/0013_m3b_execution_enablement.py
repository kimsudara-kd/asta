"""M3-B step C: time-limited, releaser-only, default-OFF runtime enablement of testnet execution

Revision ID: 0013
Revises: 0012

The execution gateway only ever ALLOWS the Binance Spot Testnet (EXCHANGE_DEMO) and only when ALL of these hold: the reviewed
code constant, the latch-free kill switch, and this database state. Nothing here sends or receives an order.

* ``execution_enablement`` has one row per scope (only BINANCE_TESTNET). ``enabled_until`` defaults to the epoch (OFF).
* Turning it ON is only possible through ``enable_testnet_execution(hours, reason)``: the caller (``session_user``) must be a
  listed kill-switch releaser (one login per human), hours 1..24, an auditable reason of >= 10 characters, and the kill
  switch must not be latched. The function writes an append-only ENABLE event; a guard trigger refuses any other UPDATE that
  would turn it on (so a bare UPDATE by any role, including the table owner, is refused unless a matching event exists in the
  same transaction written by a listed releaser).
* Turning it OFF is always allowed (``disable_testnet_execution``) and happens automatically (AUTO_OFF event) the moment the
  kill switch latches. A later release of the kill switch does NOT re-enable anything.
* ``execution_enabled(scope)`` is what the gateway reads: true only while enabled_until is in the future AND the kill switch is
  not latched. It is read-only and fails closed in the application on any error.
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE execution_enablement (
          scope varchar(32) PRIMARY KEY CHECK (scope IN ('BINANCE_TESTNET')),
          enabled_until timestamptz NOT NULL DEFAULT 'epoch',
          enabled_by varchar(64) NOT NULL DEFAULT 'none',
          reason text NOT NULL DEFAULT '',
          updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("INSERT INTO execution_enablement (scope) VALUES ('BINANCE_TESTNET')")
    op.execute(
        """
        CREATE TABLE execution_enablement_events (
          id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          at timestamptz NOT NULL DEFAULT now(),
          action varchar(8) NOT NULL CHECK (action IN ('ENABLE', 'DISABLE', 'AUTO_OFF')),
          actor varchar(64) NOT NULL CHECK (length(trim(actor)) > 0),
          hours integer,
          reason text NOT NULL
        )
        """
    )
    op.execute(
        "CREATE TRIGGER exe_append_only BEFORE UPDATE OR DELETE ON execution_enablement_events"
        " FOR EACH ROW EXECUTE FUNCTION append_only_guard()"
    )
    op.execute(
        "CREATE TRIGGER exe_no_truncate BEFORE TRUNCATE ON execution_enablement_events"
        " FOR EACH STATEMENT EXECUTE FUNCTION append_only_guard()"
    )
    op.execute(
        """
        CREATE FUNCTION execution_enablement_guard() RETURNS trigger AS $$
        BEGIN
          IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
            RAISE EXCEPTION 'execution_enablement is never deleted';
          END IF;
          IF NEW.enabled_until > now() THEN
            IF NEW.enabled_until > now() + interval '24 hours 1 minute' THEN
              RAISE EXCEPTION 'execution enablement is limited to 24 hours';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM kill_switch_releasers r WHERE r.name = session_user::text) THEN
              RAISE EXCEPTION 'enablement refused: % is not an authorised releaser', session_user;
            END IF;
            IF NOT EXISTS (
              SELECT 1 FROM execution_enablement_events e
              WHERE e.action = 'ENABLE' AND e.actor = session_user::text AND e.at = now()
            ) THEN
              RAISE EXCEPTION 'enablement refused: no ENABLE event was logged in this transaction';
            END IF;
          END IF;
          NEW.updated_at = now();
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for ev, sql in (
        ("upd", "BEFORE UPDATE ON execution_enablement FOR EACH ROW"),
        ("del", "BEFORE DELETE ON execution_enablement FOR EACH ROW"),
        ("trunc", "BEFORE TRUNCATE ON execution_enablement FOR EACH STATEMENT"),
    ):
        op.execute(
            f"CREATE TRIGGER execution_enablement_guard_{ev} {sql} EXECUTE FUNCTION execution_enablement_guard()"
        )
    op.execute(
        """
        CREATE FUNCTION enable_testnet_execution(p_hours integer, p_reason text) RETURNS timestamptz AS $$
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
    op.execute(
        """
        CREATE FUNCTION disable_testnet_execution(p_reason text) RETURNS boolean AS $$
        DECLARE who text := session_user::text; was_on boolean;
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM kill_switch_releasers r WHERE r.name = who) THEN
            RAISE EXCEPTION 'disable refused: % is not an authorised releaser', who;
          END IF;
          IF p_reason IS NULL OR length(trim(p_reason)) < 10 THEN
            RAISE EXCEPTION 'disable refused: an auditable reason of at least 10 characters is required';
          END IF;
          SELECT enabled_until > now() INTO was_on FROM execution_enablement WHERE scope = 'BINANCE_TESTNET';
          INSERT INTO execution_enablement_events (action, actor, reason) VALUES ('DISABLE', who, p_reason);
          UPDATE execution_enablement SET enabled_until = 'epoch', enabled_by = who, reason = p_reason
            WHERE scope = 'BINANCE_TESTNET';
          RETURN was_on;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
        """
    )
    op.execute(
        """
        CREATE FUNCTION execution_enabled(p_scope text) RETURNS boolean AS $$
          SELECT coalesce(
            (SELECT e.enabled_until > now() FROM execution_enablement e WHERE e.scope = p_scope), false)
            AND NOT coalesce((SELECT k.active FROM kill_switch_state k WHERE k.id = 1), true)
        $$ LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
        """
    )
    op.execute(
        """
        CREATE FUNCTION execution_auto_off() RETURNS trigger AS $$
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
        "CREATE TRIGGER kill_switch_auto_off AFTER UPDATE ON kill_switch_state"
        " FOR EACH ROW EXECUTE FUNCTION execution_auto_off()"
    )
    for fn in (
        "enable_testnet_execution(integer, text)",
        "disable_testnet_execution(text)",
        "execution_enabled(text)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {fn} FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS kill_switch_auto_off ON kill_switch_state")
    op.execute("DROP FUNCTION IF EXISTS execution_auto_off()")
    op.execute("DROP FUNCTION IF EXISTS execution_enabled(text)")
    op.execute("DROP FUNCTION IF EXISTS disable_testnet_execution(text)")
    op.execute("DROP FUNCTION IF EXISTS enable_testnet_execution(integer, text)")
    op.execute("DROP TABLE IF EXISTS execution_enablement_events")
    op.execute("DROP TABLE IF EXISTS execution_enablement")
    op.execute("DROP FUNCTION IF EXISTS execution_enablement_guard()")
