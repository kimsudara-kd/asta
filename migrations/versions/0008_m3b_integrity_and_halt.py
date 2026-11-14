"""M3-B offline remediation (ASTA-DECISION-007): fill integrity, durable replay state, latched kill switch

Revision ID: 0008
Revises: 0007

Schema only; nothing here sends or receives an order.
* order_fills: a BEFORE INSERT guard locks the intent row (serialises concurrent workers) and refuses
  a fill for a PENDING/REJECTED order, a LIMIT fill outside its limit, and any fill that would push
  the cumulative quantity (or, for quote-sized orders, the cumulative quote value) past the order.
* order_fill_conflicts: append-only record of every conflicting / refused fill. Financial conflicts
  are never silently discarded.
* channel_senders / channel_nonces: transactional replay and expiry state of the intent channel;
  the per-sender sequence can only increase, so a process restart cannot re-accept a message.
* kill_switch_state / kill_switch_events / kill_switch_releasers: one latched halt row. It can only be
  tripped through trip_kill_switch() and released through release_kill_switch(), which requires the
  actor to be listed in kill_switch_releasers (empty by default: nobody) and a reason of at least 10
  characters; a direct UPDATE that clears the latch is refused. Events are append-only.
* order_intents: no state may advance to DISPATCHED while the kill switch is latched (every worker).
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

CAUSES = (
    "MANUAL", "RISK_HALT", "RECONCILIATION_ANOMALY", "UNKNOWN_EXCHANGE_ORDER", "AUTH_FAILURES",
    "DAILY_CAP_HIT", "CLOCK_SKEW", "EXCHANGE_RESET", "RATE_LIMIT_BAN", "FILL_CONFLICT",
    "FILL_REJECTED", "RECONCILIATION_MISMATCH",
)  # fmt: skip


def upgrade() -> None:
    causes_sql = ", ".join(f"'{c}'" for c in CAUSES)
    # ---------------------------------------------------------------- kill switch
    op.create_table(
        "kill_switch_state",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name="ck_ks_single_row"),
    )
    op.execute("INSERT INTO kill_switch_state (id, active) VALUES (1, false)")
    op.create_table(
        "kill_switch_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("action", sa.String(length=8), nullable=False),
        sa.Column("cause", sa.String(length=32), nullable=True),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.CheckConstraint("action IN ('TRIP', 'RELEASE')", name="ck_kse_action"),
        sa.CheckConstraint(f"cause IS NULL OR cause IN ({causes_sql})", name="ck_kse_cause"),
        sa.CheckConstraint("length(trim(actor)) > 0", name="ck_kse_actor"),
    )
    op.create_table(
        "kill_switch_releasers",
        sa.Column("name", sa.String(length=64), primary_key=True),
        sa.Column(
            "added_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )
    op.execute(
        """
        CREATE FUNCTION kill_switch_state_guard() RETURNS trigger AS $$
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
    )
    for ev, sql in (
        ("upd", "BEFORE UPDATE ON kill_switch_state FOR EACH ROW"),
        ("del", "BEFORE DELETE ON kill_switch_state FOR EACH ROW"),
        ("trunc", "BEFORE TRUNCATE ON kill_switch_state FOR EACH STATEMENT"),
    ):
        op.execute(
            f"CREATE TRIGGER kill_switch_state_guard_{ev} {sql} EXECUTE FUNCTION kill_switch_state_guard()"
        )
    op.execute(
        """
        CREATE FUNCTION append_only_guard() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER kse_append_only BEFORE UPDATE OR DELETE ON kill_switch_events FOR EACH ROW EXECUTE FUNCTION append_only_guard()"
    )
    op.execute(
        "CREATE TRIGGER kse_no_truncate BEFORE TRUNCATE ON kill_switch_events FOR EACH STATEMENT EXECUTE FUNCTION append_only_guard()"
    )
    op.execute(
        """
        CREATE FUNCTION trip_kill_switch(p_cause text, p_actor text, p_reason text) RETURNS void AS $$
        BEGIN
          PERFORM 1 FROM kill_switch_state WHERE id = 1 FOR UPDATE;
          INSERT INTO kill_switch_events (action, cause, actor, reason) VALUES ('TRIP', p_cause, p_actor, p_reason);
          UPDATE kill_switch_state SET active = true WHERE id = 1;
        END;
        $$ LANGUAGE plpgsql
        """
    )
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
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE FUNCTION order_intents_kill_guard() RETURNS trigger AS $$
        BEGIN
          IF NEW.state = 'DISPATCHED' AND OLD.state IS DISTINCT FROM 'DISPATCHED'
             AND (SELECT active FROM kill_switch_state WHERE id = 1) THEN
            RAISE EXCEPTION 'kill switch latched: % cannot advance to DISPATCHED', OLD.decision_id;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER order_intents_kill_guard_upd BEFORE UPDATE ON order_intents FOR EACH ROW EXECUTE FUNCTION order_intents_kill_guard()"
    )

    # ---------------------------------------------------------------- fills
    op.create_table(
        "order_fill_conflicts",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "decision_id",
            sa.String(length=32),
            sa.ForeignKey("order_intents.decision_id"),
            nullable=False,
        ),
        sa.Column("trade_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("existing", sa.Text(), nullable=True),
        sa.Column("offered", sa.Text(), nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "kind IN ('CONFLICT', 'OVERFILL', 'BAD_STATE', 'OUT_OF_LIMIT')", name="ck_ofc_kind"
        ),
    )
    op.create_index("ix_order_fill_conflicts_decision", "order_fill_conflicts", ["decision_id"])
    op.execute(
        "CREATE TRIGGER ofc_append_only BEFORE UPDATE OR DELETE ON order_fill_conflicts FOR EACH ROW EXECUTE FUNCTION append_only_guard()"
    )
    op.execute(
        "CREATE TRIGGER ofc_no_truncate BEFORE TRUNCATE ON order_fill_conflicts FOR EACH STATEMENT EXECUTE FUNCTION append_only_guard()"
    )
    op.execute(
        """
        CREATE FUNCTION order_fills_guard() RETURNS trigger AS $$
        DECLARE o record; sum_q numeric; sum_v numeric;
        BEGIN
          SELECT state, quantity, quote_notional, limit_price, side, order_type INTO o
            FROM order_intents WHERE decision_id = NEW.decision_id FOR UPDATE;
          IF NOT FOUND THEN RAISE EXCEPTION 'BAD_STATE: fill for an unknown order'; END IF;
          IF o.state IN ('PENDING', 'REJECTED') THEN
            RAISE EXCEPTION 'BAD_STATE: fill for an order in state %', o.state;
          END IF;
          IF o.order_type = 'LIMIT' AND ((o.side = 'BUY' AND NEW.price > o.limit_price)
                                      OR (o.side = 'SELL' AND NEW.price < o.limit_price)) THEN
            RAISE EXCEPTION 'OUT_OF_LIMIT: fill price % violates the limit %', NEW.price, o.limit_price;
          END IF;
          IF o.quantity IS NOT NULL THEN
            SELECT coalesce(sum(quantity), 0) INTO sum_q FROM order_fills WHERE decision_id = NEW.decision_id;
            IF sum_q + NEW.quantity > o.quantity THEN
              RAISE EXCEPTION 'OVERFILL: % + % exceeds the ordered quantity %', sum_q, NEW.quantity, o.quantity;
            END IF;
          ELSE
            SELECT coalesce(sum(quantity * price), 0) INTO sum_v FROM order_fills WHERE decision_id = NEW.decision_id;
            IF sum_v + NEW.quantity * NEW.price > o.quote_notional THEN
              RAISE EXCEPTION 'OVERFILL: quote value % + % exceeds the ordered notional %', sum_v, NEW.quantity * NEW.price, o.quote_notional;
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER order_fills_guard_ins BEFORE INSERT ON order_fills FOR EACH ROW EXECUTE FUNCTION order_fills_guard()"
    )

    # ---------------------------------------------------------------- channel replay state
    op.create_table(
        "channel_senders",
        sa.Column("sender", sa.String(length=64), primary_key=True),
        sa.Column("high_seq", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("high_seq >= 0", name="ck_cs_seq"),
    )
    op.create_table(
        "channel_nonces",
        sa.Column(
            "sender", sa.String(length=64), sa.ForeignKey("channel_senders.sender"), nullable=False
        ),
        sa.Column("nonce", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.Column(
            "accepted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("sender", "nonce"),
    )
    op.execute(
        """
        CREATE FUNCTION channel_senders_guard() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'channel_senders rows are never deleted'; END IF;
          IF NEW.high_seq < OLD.high_seq THEN
            RAISE EXCEPTION 'channel sequence can only increase (% -> %)', OLD.high_seq, NEW.high_seq;
          END IF;
          NEW.updated_at = now();
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER channel_senders_guard_trg BEFORE UPDATE OR DELETE ON channel_senders FOR EACH ROW EXECUTE FUNCTION channel_senders_guard()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS channel_senders_guard_trg ON channel_senders")
    op.execute("DROP FUNCTION IF EXISTS channel_senders_guard()")
    op.drop_table("channel_nonces")
    op.drop_table("channel_senders")
    op.execute("DROP TRIGGER IF EXISTS order_fills_guard_ins ON order_fills")
    op.execute("DROP FUNCTION IF EXISTS order_fills_guard()")
    op.execute("DROP TRIGGER IF EXISTS ofc_no_truncate ON order_fill_conflicts")
    op.execute("DROP TRIGGER IF EXISTS ofc_append_only ON order_fill_conflicts")
    op.drop_table("order_fill_conflicts")
    op.execute("DROP TRIGGER IF EXISTS order_intents_kill_guard_upd ON order_intents")
    op.execute("DROP FUNCTION IF EXISTS order_intents_kill_guard()")
    op.execute("DROP FUNCTION IF EXISTS release_kill_switch(text, text)")
    op.execute("DROP FUNCTION IF EXISTS trip_kill_switch(text, text, text)")
    op.execute("DROP TRIGGER IF EXISTS kse_no_truncate ON kill_switch_events")
    op.execute("DROP TRIGGER IF EXISTS kse_append_only ON kill_switch_events")
    for ev in ("trunc", "del", "upd"):
        op.execute(f"DROP TRIGGER IF EXISTS kill_switch_state_guard_{ev} ON kill_switch_state")
    op.drop_table("kill_switch_releasers")
    op.drop_table("kill_switch_events")
    op.drop_table("kill_switch_state")
    op.execute("DROP FUNCTION IF EXISTS kill_switch_state_guard()")
    op.execute("DROP FUNCTION IF EXISTS append_only_guard()")
