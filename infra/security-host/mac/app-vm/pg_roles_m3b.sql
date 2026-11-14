-- ASTA M3-B-R3: least-privilege PostgreSQL roles for the order-path services (design + tested draft).
-- Applied by the provisioning step on asta-app (NOT run by anything yet). Authentication is peer-only
-- (pg_ident map OS user -> role); no role has a password. Run as the database owner after migrations.
--   asta_bridge  : the supervised risk-halt bridge. Can LATCH the kill switch and beat its heartbeat;
--                  cannot release, cannot write the halt tables directly, cannot add releasers.
--   asta_orders  : the order-path worker (dispatcher, fill ingest, reconciler). Can write intents/fills/
--                  replay state and trip the switch; cannot release, cannot touch releasers.
--   asta_monitor : reads heartbeat/kill-switch state; may only APPEND alert-state changes to the audit chain.
--   asta_release : the operator role that releases a halt (only through release_kill_switch(reason), and only
--                  for a session user listed in kill_switch_releasers). Nothing else.
-- Everything is deny-by-default: the migrations REVOKE EXECUTE on the halt functions from PUBLIC.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'asta_bridge')  THEN CREATE ROLE asta_bridge  NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'asta_orders')  THEN CREATE ROLE asta_orders  NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'asta_release') THEN CREATE ROLE asta_release NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'asta_monitor') THEN CREATE ROLE asta_monitor NOLOGIN; END IF;
END $$;

-- bridge: read paper halt state, record what it has seen, trip, beat, write its audit rows
GRANT SELECT ON paper_sessions, paper_session_states, kill_switch_state, risk_halt_bridge_heartbeat TO asta_bridge;
GRANT SELECT, INSERT ON risk_halt_bridge_seen, audit_events TO asta_bridge;
GRANT USAGE ON SEQUENCE audit_events_id_seq TO asta_bridge;
GRANT EXECUTE ON FUNCTION trip_kill_switch(text, text, text), bridge_heartbeat() TO asta_bridge;

-- order-path worker
GRANT SELECT, INSERT, UPDATE ON order_intents TO asta_orders;
GRANT SELECT, INSERT ON order_intent_events, order_fills, order_fill_conflicts, channel_nonces, audit_events TO asta_orders;
GRANT SELECT, INSERT, UPDATE ON channel_senders TO asta_orders;
GRANT SELECT ON order_intent_transitions, kill_switch_state, kill_switch_events, risk_halt_bridge_heartbeat TO asta_orders;
GRANT DELETE ON channel_nonces TO asta_orders;  -- pruning expired replay state only
GRANT USAGE ON SEQUENCE audit_events_id_seq, order_intent_events_id_seq, order_fills_id_seq, order_fill_conflicts_id_seq TO asta_orders;
GRANT EXECUTE ON FUNCTION trip_kill_switch(text, text, text) TO asta_orders;

-- operator release role
GRANT SELECT ON kill_switch_state, kill_switch_events, kill_switch_releasers TO asta_release;
GRANT SELECT, INSERT ON audit_events TO asta_release;
GRANT USAGE ON SEQUENCE audit_events_id_seq TO asta_release;
-- the releaser is session_user (the authenticated DB login), which must be listed in kill_switch_releasers.
-- One LOGIN role per human, a member of asta_release (e.g. CREATE ROLE rel_alice LOGIN IN ROLE asta_release;).
GRANT EXECUTE ON FUNCTION release_kill_switch(text) TO asta_release;

-- failure monitor: read-only
GRANT SELECT ON kill_switch_state, risk_halt_bridge_heartbeat TO asta_monitor;
-- the monitor records alert-state CHANGES in the append-only audit chain (and nothing else)
GRANT SELECT, INSERT ON audit_events TO asta_monitor;
GRANT USAGE ON SEQUENCE audit_events_id_seq TO asta_monitor;
