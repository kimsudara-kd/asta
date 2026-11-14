"""M3-B preparation: durable order-intent store (schema only; no connector, nothing sends orders)

Revision ID: 0006
Revises: 0005

The exact-once gate of docs/m3b/ORDER-PATH-DESIGN.md as database constraints:
* decision_id is the PRIMARY KEY (the atomic claim) and must be a 32-hex deterministic id;
* environment can only ever be EXCHANGE_DEMO (a hard database lock mirroring the live-lock: there is no
  value that could mean a real-money venue);
* sizing/side/type sanity as CHECKs; the identity and sizing columns can never change after the claim;
* state changes are legal only if (old, new) is in order_intent_transitions (seeded from the Python state
  machine; a test fails if the two ever differ); intents are never deleted or truncated;
* every insert/state change is appended to order_intent_events by TRIGGER (the application cannot forget
  or falsify it); the events table is append-only.
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

STATES = (
    "PENDING",
    "DISPATCHED",
    "UNKNOWN",
    "ACCEPTED",
    "PARTIALLY_FILLED",
    "CANCEL_REQUESTED",
    "FILLED",
    "CANCELED",
    "REJECTED",
    "EXPIRED",
    "ABANDONED",
)
TRANSITIONS = [
    ("ABANDONED", "ACCEPTED"),
    ("ABANDONED", "CANCELED"),
    ("ABANDONED", "EXPIRED"),
    ("ABANDONED", "FILLED"),
    ("ABANDONED", "PARTIALLY_FILLED"),
    ("ABANDONED", "REJECTED"),
    ("ACCEPTED", "CANCELED"),
    ("ACCEPTED", "CANCEL_REQUESTED"),
    ("ACCEPTED", "EXPIRED"),
    ("ACCEPTED", "FILLED"),
    ("ACCEPTED", "PARTIALLY_FILLED"),
    ("ACCEPTED", "REJECTED"),
    ("CANCEL_REQUESTED", "ACCEPTED"),
    ("CANCEL_REQUESTED", "CANCELED"),
    ("CANCEL_REQUESTED", "CANCEL_REQUESTED"),
    ("CANCEL_REQUESTED", "EXPIRED"),
    ("CANCEL_REQUESTED", "FILLED"),
    ("CANCEL_REQUESTED", "PARTIALLY_FILLED"),
    ("CANCEL_REQUESTED", "REJECTED"),
    ("DISPATCHED", "ABANDONED"),
    ("DISPATCHED", "ACCEPTED"),
    ("DISPATCHED", "CANCELED"),
    ("DISPATCHED", "EXPIRED"),
    ("DISPATCHED", "FILLED"),
    ("DISPATCHED", "PARTIALLY_FILLED"),
    ("DISPATCHED", "REJECTED"),
    ("DISPATCHED", "UNKNOWN"),
    ("PARTIALLY_FILLED", "CANCELED"),
    ("PARTIALLY_FILLED", "CANCEL_REQUESTED"),
    ("PARTIALLY_FILLED", "EXPIRED"),
    ("PARTIALLY_FILLED", "FILLED"),
    ("PARTIALLY_FILLED", "PARTIALLY_FILLED"),
    ("PARTIALLY_FILLED", "REJECTED"),
    ("PENDING", "ABANDONED"),
    ("PENDING", "DISPATCHED"),
    ("PENDING", "REJECTED"),
    ("UNKNOWN", "ABANDONED"),
    ("UNKNOWN", "ACCEPTED"),
    ("UNKNOWN", "CANCELED"),
    ("UNKNOWN", "DISPATCHED"),
    ("UNKNOWN", "EXPIRED"),
    ("UNKNOWN", "FILLED"),
    ("UNKNOWN", "PARTIALLY_FILLED"),
    ("UNKNOWN", "REJECTED"),
]


def upgrade() -> None:
    states_sql = ", ".join(f"'{s}'" for s in STATES)
    op.create_table(
        "order_intent_transitions",
        sa.Column("src", sa.Text(), nullable=False),
        sa.Column("dst", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("src", "dst"),
    )
    op.bulk_insert(
        sa.table(
            "order_intent_transitions", sa.column("src", sa.Text()), sa.column("dst", sa.Text())
        ),
        [{"src": a, "dst": b} for a, b in TRANSITIONS],
    )
    op.create_table(
        "order_intents",
        sa.Column("decision_id", sa.String(length=32), primary_key=True),
        sa.Column("environment", sa.String(length=16), nullable=False),
        sa.Column("venue", sa.String(length=32), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("order_type", sa.String(length=8), nullable=False),
        sa.Column("quote_notional", sa.Numeric(38, 18), nullable=True),
        sa.Column("quantity", sa.Numeric(38, 18), nullable=True),
        sa.Column("limit_price", sa.Numeric(38, 18), nullable=True),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("misses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cancel_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_ts", sa.BigInteger(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("decision_id ~ '^[0-9a-f]{32}$'", name="ck_oi_decision_id"),
        sa.CheckConstraint("environment = 'EXCHANGE_DEMO'", name="ck_oi_environment_demo_only"),
        sa.CheckConstraint("side IN ('BUY', 'SELL')", name="ck_oi_side"),
        sa.CheckConstraint("order_type IN ('MARKET', 'LIMIT')", name="ck_oi_type"),
        sa.CheckConstraint("(quote_notional IS NULL) <> (quantity IS NULL)", name="ck_oi_one_size"),
        sa.CheckConstraint(
            "coalesce(quote_notional, 1) > 0 AND coalesce(quantity, 1) > 0",
            name="ck_oi_size_positive",
        ),
        sa.CheckConstraint(
            "order_type <> 'LIMIT' OR (limit_price IS NOT NULL AND limit_price > 0)",
            name="ck_oi_limit_price",
        ),
        sa.CheckConstraint(f"state IN ({states_sql})", name="ck_oi_state"),
        sa.CheckConstraint(
            "attempts >= 0 AND misses >= 0 AND cancel_attempts >= 0", name="ck_oi_counters"
        ),
    )
    op.create_index("ix_order_intents_state", "order_intents", ["state"])
    op.create_table(
        "order_intent_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "decision_id",
            sa.String(length=32),
            sa.ForeignKey("order_intents.decision_id"),
            nullable=False,
        ),
        sa.Column("from_state", sa.String(length=24), nullable=True),
        sa.Column("to_state", sa.String(length=24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("cancel_attempts", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )
    op.create_index("ix_order_intent_events_decision", "order_intent_events", ["decision_id", "id"])

    op.execute(
        """
        CREATE FUNCTION order_intents_guard() RETURNS trigger AS $$
        BEGIN
          IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
            RAISE EXCEPTION 'order_intents rows are never deleted';
          END IF;
          IF NEW.decision_id IS DISTINCT FROM OLD.decision_id
             OR NEW.environment IS DISTINCT FROM OLD.environment
             OR NEW.venue IS DISTINCT FROM OLD.venue
             OR NEW.symbol IS DISTINCT FROM OLD.symbol
             OR NEW.side IS DISTINCT FROM OLD.side
             OR NEW.order_type IS DISTINCT FROM OLD.order_type
             OR NEW.quote_notional IS DISTINCT FROM OLD.quote_notional
             OR NEW.quantity IS DISTINCT FROM OLD.quantity
             OR NEW.limit_price IS DISTINCT FROM OLD.limit_price
             OR NEW.created_ts IS DISTINCT FROM OLD.created_ts THEN
            RAISE EXCEPTION 'order intent % identity and sizing are immutable', OLD.decision_id;
          END IF;
          IF NEW.state IS DISTINCT FROM OLD.state THEN
            IF NOT EXISTS (SELECT 1 FROM order_intent_transitions t WHERE t.src = OLD.state AND t.dst = NEW.state) THEN
              RAISE EXCEPTION 'illegal order intent transition % -> % for %', OLD.state, NEW.state, OLD.decision_id;
            END IF;
          ELSIF NEW.attempts IS DISTINCT FROM OLD.attempts OR NEW.cancel_attempts IS DISTINCT FROM OLD.cancel_attempts THEN
            -- counters move together with a state write (re-send, cancel retry), which re-enters the same state
            -- only through an explicit transition row; a bare counter change is allowed only for those states
            IF NOT EXISTS (SELECT 1 FROM order_intent_transitions t WHERE t.src = OLD.state AND t.dst = NEW.state) THEN
              RAISE EXCEPTION 'order intent % counters cannot change without a legal transition', OLD.decision_id;
            END IF;
          END IF;
          NEW.updated_at = now();
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER order_intents_guard_upd BEFORE UPDATE ON order_intents "
        "FOR EACH ROW EXECUTE FUNCTION order_intents_guard()"
    )
    op.execute(
        "CREATE TRIGGER order_intents_guard_del BEFORE DELETE ON order_intents "
        "FOR EACH ROW EXECUTE FUNCTION order_intents_guard()"
    )
    op.execute(
        "CREATE TRIGGER order_intents_guard_trunc BEFORE TRUNCATE ON order_intents "
        "FOR EACH STATEMENT EXECUTE FUNCTION order_intents_guard()"
    )
    op.execute(
        """
        CREATE FUNCTION order_intents_audit() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'INSERT' THEN
            INSERT INTO order_intent_events (decision_id, from_state, to_state, attempts, cancel_attempts, note)
            VALUES (NEW.decision_id, NULL, NEW.state, NEW.attempts, NEW.cancel_attempts, NEW.note);
          ELSIF NEW.state IS DISTINCT FROM OLD.state OR NEW.note IS DISTINCT FROM OLD.note
                OR NEW.attempts IS DISTINCT FROM OLD.attempts OR NEW.cancel_attempts IS DISTINCT FROM OLD.cancel_attempts THEN
            INSERT INTO order_intent_events (decision_id, from_state, to_state, attempts, cancel_attempts, note)
            VALUES (NEW.decision_id, OLD.state, NEW.state, NEW.attempts, NEW.cancel_attempts, NEW.note);
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER order_intents_audit_trg AFTER INSERT OR UPDATE ON order_intents "
        "FOR EACH ROW EXECUTE FUNCTION order_intents_audit()"
    )
    op.execute(
        """
        CREATE FUNCTION order_intent_events_immutable() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'order_intent_events is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER order_intent_events_append_only BEFORE UPDATE OR DELETE ON order_intent_events "
        "FOR EACH ROW EXECUTE FUNCTION order_intent_events_immutable()"
    )
    op.execute(
        "CREATE TRIGGER order_intent_events_no_truncate BEFORE TRUNCATE ON order_intent_events "
        "FOR EACH STATEMENT EXECUTE FUNCTION order_intent_events_immutable()"
    )
    op.execute(
        "CREATE TRIGGER order_intent_transitions_append_only BEFORE UPDATE OR DELETE ON order_intent_transitions "
        "FOR EACH ROW EXECUTE FUNCTION order_intent_events_immutable()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS order_intent_transitions_append_only ON order_intent_transitions"
    )
    op.execute("DROP TRIGGER IF EXISTS order_intent_events_no_truncate ON order_intent_events")
    op.execute("DROP TRIGGER IF EXISTS order_intent_events_append_only ON order_intent_events")
    op.execute("DROP TRIGGER IF EXISTS order_intents_audit_trg ON order_intents")
    op.execute("DROP TRIGGER IF EXISTS order_intents_guard_trunc ON order_intents")
    op.execute("DROP TRIGGER IF EXISTS order_intents_guard_del ON order_intents")
    op.execute("DROP TRIGGER IF EXISTS order_intents_guard_upd ON order_intents")
    op.execute("DROP FUNCTION IF EXISTS order_intent_events_immutable()")
    op.execute("DROP FUNCTION IF EXISTS order_intents_audit()")
    op.execute("DROP FUNCTION IF EXISTS order_intents_guard()")
    op.drop_table("order_intent_events")
    op.drop_table("order_intents")
    op.drop_table("order_intent_transitions")
