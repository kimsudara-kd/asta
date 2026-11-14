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
Windows App Control note: if `alembic.exe` / `pytest.exe` are blocked, use `python -m alembic|pytest`;
mypy and gitleaks run in containers (see Checks).

## Install as an app (PWA)
The dashboard is an installable Progressive Web App (manifest, icons, service worker, offline page; bottom tab bar
on phones). Build and run it in production mode (`cd apps/web && pnpm build && pnpm start`), open it in Chrome/Edge
and use the install button in the address bar, or on a phone use **Add to Home Screen** (iOS Safari: Share ->
Add to Home Screen; Android Chrome: menu -> Install app). `pnpm run pwa:check` verifies the whole PWA in real Chrome.

* **Secure context required.** Browsers only register service workers (and offer installation) on `https://` or
  `http://localhost`. The dashboard deliberately listens on **127.0.0.1 only**, so a phone cannot reach it directly.
  Private phone access via Tailscale (tailnet-only, never public):
  1. `sh scripts/dev_stack.sh start` (database + API + dashboard; `status` / `stop` also available).
  2. `tailscale serve --bg --https=8443 http://127.0.0.1:3640` publishes it at
     `https://<machine>.<tailnet>.ts.net:8443`. Use a **separate port** (443, 8443 or 10000) so an existing Serve
     configuration is not overwritten (`tailscale serve status` first), and **never** `tailscale funnel` (public).
     Remove with `tailscale serve --https=8443 off`.
  3. Install the Tailscale app on the phone (same tailnet), open that URL, then Add to Home Screen / Install.
  The stack script sets `ASTA_COOKIE_SECURE=true` (session cookie is `Secure`, `HttpOnly`, `SameSite=Strict`). Chrome
  still accepts it on `http://127.0.0.1`; Safari does not, so use the HTTPS address there.
* **What is cached:** only public, content-hashed static files (`/_next/static/*`, icons) and the offline page. The
  worker **never** touches `/api/*`, writes, third parties, or page HTML, stores no credentials and has no push/sync
  (`apps/web/public/sw.js`; policy covered by `src/lib/sw.test.ts`). Offline you see a static "You're offline" page.
* No extra buttons or prompts are added to any page: installation uses the browser's own UI.

## Checks
```bash
uv run ruff check . && uv run ruff format --check .
uv run python -m pytest tests -q                       # default: DB tests use a throw-away embedded Postgres
ASTA_RUN_NETWORK_TESTS=1 uv run python -m pytest tests # + real public Binance/OKX smoke tests (OKX skips if unreachable)
uv run python scripts/secret_scan.py                   # dependency-free scan: tree + full git history
cd apps/web && pnpm run typecheck && pnpm run test && pnpm run build
```
Compose-backed run (PostgreSQL 16.4): `docker compose -f infra/docker-compose.yml --profile test up -d --wait db db_test`,
then `ASTA_TEST_DATABASE_URL=postgresql+pg8000://asta_test:asta_test_only@127.0.0.1:5641/asta_test` for pytest.

**macOS / Linux test database (verified on macOS 26 arm64):** use a TCP PostgreSQL 16 running in **UTC**, not the embedded
`pgserver` unix-socket default (large multi-row INSERTs hang on its unix socket; a non-UTC server timezone fails ~14 tests):
```bash
docker compose -f infra/docker-compose.yml --profile test up -d --wait db_test   # or any PG16 started with -c timezone=UTC
export ASTA_TEST_DATABASE_URL=postgresql+pg8000://asta_test:asta_test_only@127.0.0.1:5641/asta_test
uv run python -m pytest tests -q          # expected clone-only failure: test_frozen_evidence (needs the local m0/m1/m2 tags)
```
No Docker? `sh scripts/dev_pg_tcp.sh start|stop|status` runs a local TCP PostgreSQL 16 in UTC on 127.0.0.1:5640 (dev DB `asta`, test DB
`asta_test`, compose credentials) and prints the URLs; data lives in the git-ignored `infra/data/`. On macOS get pnpm with
`npm install -g pnpm@10 --prefix ~/.local` (Node 26 has no corepack).

**mypy / gitleaks (native binaries may be blocked on Windows App Control — run them in containers):**
```bash
docker run --rm -v "$PWD:/src:ro" -w /src -e UV_PROJECT_ENVIRONMENT=/opt/venv -e UV_LINK_MODE=copy python:3.12-slim   sh -c "pip install -q uv && uv sync --frozen --no-install-project -q && /opt/venv/bin/python -m mypy"
docker run --rm -v "$PWD:/repo:ro" -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0=/repo   ghcr.io/gitleaks/gitleaks:latest git /repo -c /repo/.gitleaks.toml --redact
```

**Browser acceptance (Playwright + system Chrome, disposable `asta_e2e` DB on the compose test Postgres):**
```bash
cd apps/web && node e2e/run.mjs     # live-data, no-data and aging phases; screenshots -> docs/evidence/m0-r1/screenshots
```
Set `ASTA_TEST_DATABASE_URL` to use the compose test DB (`--profile test`, port 5641) instead.

## M2 — continuous paper trading (virtual funds only; public market data only)
```bash
# database migrated to head (alembic 0004), then in one terminal:
uv run python -m asta.paper_service         # public feeds -> observations -> durable STEP jobs -> worker
#   ASTA_PAPER_VENUES=BINANCE uv run ...    # restrict venues; a venue that fails verification is DISABLED with a reason
uv run python scripts/paper_observation.py create "my session" 1h   # warm-up from public REST history
uv run python scripts/paper_observation.py start <session-id>       # or use the Paper trading pages
uv run python scripts/paper_observation.py status | gate <session-id> --hours 72
```
The service has no credentials, no signer and no order path; paper orders are rows produced by the offline simulator
(see ADR-0009..0011, `docs/architecture/m2-plan.md`, `docs/evidence/M2-acceptance.md`).
