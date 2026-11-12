# ADR-0001: Modular monolith, single Python package

Status: accepted (M0) | Date: 2026-11-12

**Context.** Handoff §19 proposes a packages tree and says teams may optimise layout after audit but must preserve boundaries. At USD 1,000 scale a set of microservices is harder to secure and operate.

**Decision.** One Python distribution `asta` under `src/asta/` with sub-packages as modules, one FastAPI process, and (from M2) one worker. The web UI is a separate Next.js app in `apps/web`. Module boundaries are enforced by an import-graph test (`tests/live_lock/test_import_boundaries.py`).

| Module | Owns | May import |
|---|---|---|
| `core` | Decimal helpers, environments, DTOs, IDs, clock | nothing in asta |
| `audit` | append-only audit events + hash chain | core |
| `risk` | default-deny gateway policy, invariants | core, audit |
| `exchanges.common` / `.binance` / `.okx` | PUBLIC market-data adapters only (M0) | core |
| `market_data` | normalization, dedupe, freshness, reconnect | core, exchanges |
| `auth` | users, argon2, TOTP, sessions, RBAC | core, audit |
| `db` | SQLAlchemy models, session | core |
| `api` | FastAPI routes | all of the above |

Redis is deferred (no demonstrated need).
