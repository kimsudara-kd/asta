# ASTA M0 system overview

Modular monolith (ADR-0001): one FastAPI process + Postgres, Next.js UI proxying `/api` same-origin.

```
Binance public REST/WS ─┐                              ┌─ /v1/overview /markets /risk
OKX public REST/WS ─────┼─> exchanges/* ─> market_data ─┤  (RBAC, read-only)
(or FIXTURE feed)       │   (egress allowlist)  dedupe, │
                        │                       freshness,
                        │                       gap, clock
                        │                              └─ risk.policy.evaluate ─> halt flags
Operator ─> web ─> api ─> auth (Argon2id+TOTP+RBAC) ─> db (audit hash chain, append-only)
                    risk.gateway.authorize(...) ──> ALWAYS DENY in M0
```

Module ownership and allowed imports are enforced by `tests/live_lock/test_import_boundaries.py`.

## Data labelling
Every market event/quote carries `source` = `LIVE` or `FIXTURE`; the API and UI never show fixture
data as live, and unknown/old data is `STALE` (fail-closed).

## Not in M0
Order routing, adapters' authenticated/trading methods, portfolio/ledger tables, strategies,
backtesting, paper trading, Redis, a `LIVE` environment.
