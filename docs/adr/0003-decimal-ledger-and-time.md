# ADR-0003: Decimal money, UTC time, immutable records

Status: accepted (M0)

- All prices, sizes and balances are `decimal.Decimal` in Python and `NUMERIC(38,18)` in PostgreSQL. Floats are rejected by the canonical models (`core.money.to_decimal` refuses `float`; JSON numbers from exchanges are parsed with `parse_float=Decimal`).
- Every market event carries `exchange_ts` (venue), `ingested_ts` (local, UTC) and `environment`. All timestamps are timezone-aware UTC; naive datetimes are rejected.
- Audit events are append-only: a DB trigger rejects UPDATE/DELETE on `audit_events`, and each row stores `prev_hash` + `row_hash` (SHA-256 chain) for tamper evidence.
- Dedupe key for market events: `(venue, symbol, channel, venue_event_id)`.
