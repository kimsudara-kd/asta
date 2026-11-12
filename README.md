# ASTA — AI Spot Trading Agent (M0: foundation and safety)

Standalone, spot-only crypto research platform for Binance and OKX.
**M0 = public market data + safety skeleton. There is no order execution, no `LIVE` environment, no
exchange credentials and no real funds anywhere in this build.** See `docs/adr/` and
`docs/evidence/M0-acceptance.md`.

## Layout
```
src/asta/      core, audit, risk, exchanges/{common,binance,okx}, market_data, db, auth, api
apps/web/      Next.js dashboard (Overview, Markets, Risk, Audit)
migrations/    Alembic
infra/         docker-compose.yml (own project/ports/volume)
tests/         unit, contracts, live_lock, integration (+ fixtures)
scripts/       secret_scan.py, dev_db.py
docs/          adr, exchange-matrix, evidence, architecture
```

## Local startup
Prereqs: Python 3.12, `uv`, Node 24 + pnpm, Docker (or the embedded-Postgres fallback below).

```bash
# 1. database (own ports: 5640 dev, 5641 test)
docker compose -f infra/docker-compose.yml up -d db
#   no Docker?  uv run python scripts/dev_db.py infra/data/pg   -> prints a URI; set
#               ASTA_DATABASE_URL to it with scheme postgresql+pg8000://

# 2. config
cp .env.example .env        # set ASTA_SECRET_KEY to a long random value; NEVER add exchange keys

# 3. migrate + first user (password via prompt; operator/reviewer/admin get a TOTP URI shown once)
uv sync
uv run python -m alembic upgrade head
uv run python -m asta.cli create-user --username me --role reviewer

# 4. API (127.0.0.1:4640) -- add ASTA_FIXTURE_VENUES=OKX if OKX is unreachable from your network
uv run python -m asta.main

# 5. dashboard (127.0.0.1:3640, proxies /api -> API)
cd apps/web && pnpm install && pnpm build && pnpm start
```
Windows App Control note: if `alembic.exe` / `pytest.exe` / `mypy.exe` are blocked, use
`python -m alembic|pytest`. See "Limitations" in the evidence file for mypy.

## Checks
```bash
uv run ruff check . && uv run ruff format --check .
uv run python -m pytest tests -q                       # DB tests start a throw-away embedded Postgres
ASTA_RUN_NETWORK_TESTS=1 uv run python -m pytest tests # + real public Binance/OKX smoke tests
uv run python scripts/secret_scan.py                   # working tree + full git history
cd apps/web && pnpm run typecheck && pnpm run test && pnpm run build
```
Set `ASTA_TEST_DATABASE_URL` to use the compose test DB (`--profile test`, port 5641) instead.
