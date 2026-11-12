# ADR-0005: Research module is pure and offline; reuse of M0 contracts

Status: accepted (M1) | Date: 2026-11-12

**Context.** M1 adds an offline backtesting engine. M0 residual R-3 (no infrastructure egress enforcement) means new code must not be able to reach the network or exchanges.

**Decision.**
1. New package `asta.research` (data, sim, strategies, analytics, validation, advisory). It is **pure**: it may import only `asta.core`, `asta.risk.policy` (default limits only) and itself. It must not import `httpx`, `websockets`, `socket`, `urllib`, `requests`, `asyncio` network helpers, `asta.exchanges`, `asta.market_data`, `asta.api`, `asta.auth`, `asta.db`. An AST test enforces this (also closes the "direct HTTP client outside exchanges/" gap for research code).
2. Persistence lives in `asta.db.research_store`; HTTP in `asta.api.research`; the bounded job worker in `asta.research_jobs` (thread, DB-backed). The pure engine never touches a database.
3. **Reuse, not duplicate:** `Environment.BACKTEST` (audit records of research actions), `Venue`, `asta.core.money` (Decimal rules, `loads_decimal`), `asta.core.timeutil.require_utc`, `SymbolRules` (converted into the versioned `RuleProfile`), `asta.risk.policy.RiskPolicy` (default percentages), the audit hash chain, Alembic/NUMERIC(38,18)/timestamptz conventions.
4. The research `Bar` is a slim frozen dataclass (no ingest/event metadata). It is **not** a second exchange DTO: `CandleEvent` stays the live-feed contract; a converter exists.
5. The `ExecutionGateway` and every M0 live-lock test are unchanged. Simulated orders live in `research.sim` and have no connection to the gateway or to any adapter.
6. **Data acquisition is separate from the engine.** The engine and importer read local files only. A standalone tool (`scripts/fetch_binance_klines.py`) may download public Binance klines through the existing M0 egress-checked client into local files with provenance; OKX cannot be fetched from this network (R-1) and is never circumvented.
