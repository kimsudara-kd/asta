#!/bin/sh
# Local ASTA stack for development / phone access: Postgres (TCP, UTC) + API :4640 + dashboard :3640.
# Rootless; everything listens on 127.0.0.1 only. Publish the dashboard to your private tailnet with
#   tailscale serve --bg --https=8443 http://127.0.0.1:3640        (tailnet only; never use `funnel`)
# Usage: sh scripts/dev_stack.sh start | stop | status
# State lives in infra/data/run (git-ignored): secret.key (0600, generated once; it encrypts TOTP secrets, so keep
# it: a new key invalidates enrolled authenticators), api.pid, web.pid, api.log, web.log.
# `stop` only ever stops the processes THIS script started (by recorded pid).
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN="$ROOT/infra/data/run"
mkdir -p "$RUN"
export PATH="$HOME/.local/bin:$PATH"
[ -s "$RUN/secret.key" ] || { umask 077; openssl rand -hex 24 > "$RUN/secret.key"; }
export ASTA_DATABASE_URL="${ASTA_DATABASE_URL:-postgresql+pg8000://asta:asta_dev_only@127.0.0.1:5640/asta}"
export ASTA_SECRET_KEY="$(cat "$RUN/secret.key")"
export ASTA_FIXTURE_VENUES="${ASTA_FIXTURE_VENUES-BINANCE,OKX}"   # labelled fixture feeds: no network needed
export ASTA_COOKIE_SECURE="${ASTA_COOKIE_SECURE-true}"            # session cookie only over HTTPS / localhost

alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }
case "${1:-status}" in
  start)
    sh "$ROOT/scripts/dev_pg_tcp.sh" start >/dev/null
    (cd "$ROOT" && uv run python -m alembic upgrade head >/dev/null 2>&1)
    alive "$RUN/api.pid" || { (cd "$ROOT" && nohup uv run python -m asta.main > "$RUN/api.log" 2>&1 & echo $! > "$RUN/api.pid"); }
    [ -d "$ROOT/apps/web/.next" ] || (cd "$ROOT/apps/web" && pnpm install --frozen-lockfile >/dev/null && pnpm build >/dev/null)
    alive "$RUN/web.pid" || { (cd "$ROOT/apps/web" && nohup pnpm start > "$RUN/web.log" 2>&1 & echo $! > "$RUN/web.pid"); }
    for i in $(seq 1 40); do
      curl -fsS -o /dev/null http://127.0.0.1:4640/health 2>/dev/null && curl -fsS -o /dev/null http://127.0.0.1:3640/login 2>/dev/null && break
      sleep 1
    done
    sh "$0" status
    ;;
  stop)
    for n in web api; do
      if alive "$RUN/$n.pid"; then
        pkill -P "$(cat "$RUN/$n.pid")" 2>/dev/null || true   # children of OUR recorded process only
        kill "$(cat "$RUN/$n.pid")" 2>/dev/null || true
      fi
      rm -f "$RUN/$n.pid"
    done
    echo "stopped dashboard and API (database left running: sh scripts/dev_pg_tcp.sh stop)"
    ;;
  status)
    printf 'api       : '; curl -fsS --max-time 3 http://127.0.0.1:4640/health 2>/dev/null || echo "down"
    printf '\ndashboard : '; curl -s -o /dev/null -w '%{http_code}\n' --max-time 3 http://127.0.0.1:3640/login 2>/dev/null || echo "down"
    printf 'database  : '; sh "$ROOT/scripts/dev_pg_tcp.sh" status
    ;;
  *) echo "usage: $0 start|stop|status" >&2; exit 2 ;;
esac
