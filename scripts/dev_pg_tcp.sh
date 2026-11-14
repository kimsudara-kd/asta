#!/bin/sh
# Local TCP PostgreSQL 16 (UTC) for macOS/Linux machines without Docker - dev-only, 127.0.0.1 only.
# Uses the binaries bundled with the dev-only `pgserver` package, but over TCP: pgserver's default
# unix-socket mode hangs on large INSERTs on macOS and its URI is not understood by pg8000.
# One server on 127.0.0.1:5640 (the compose dev port) holding both databases and the compose credentials:
#   dev  : postgresql+pg8000://asta:asta_dev_only@127.0.0.1:5640/asta
#   test : postgresql+pg8000://asta_test:asta_test_only@127.0.0.1:5640/asta_test
# Usage: sh scripts/dev_pg_tcp.sh start|stop|status
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$(ls -d "$ROOT"/.venv/lib/python3.*/site-packages/pgserver/pginstall/bin 2>/dev/null | head -1)"
[ -n "$BIN" ] || { echo "pgserver binaries not found: run 'uv sync' first" >&2; exit 1; }
DATA="$ROOT/infra/data/pgtcp"          # infra/data/ is git-ignored
SOCK="$ROOT/infra/data/pgsock"
OPTS="-c listen_addresses=127.0.0.1 -c port=5640 -c unix_socket_directories=$SOCK -c timezone=UTC -c log_timezone=UTC"

case "${1:-status}" in
  start)
    if [ ! -d "$DATA/base" ]; then
      mkdir -p "$DATA"
      "$BIN/initdb" -D "$DATA" -U postgres --auth=trust -E UTF8 >/dev/null
    fi
    mkdir -p "$SOCK"
    "$BIN/pg_ctl" -D "$DATA" status >/dev/null 2>&1 || "$BIN/pg_ctl" -D "$DATA" -o "$OPTS" -l "$DATA/server.log" -w start >/dev/null
    P="$BIN/psql -h 127.0.0.1 -p 5640 -U postgres -d postgres -Atq"
    for spec in "asta:asta_dev_only:asta" "asta_test:asta_test_only:asta_test"; do
      u="${spec%%:*}"; rest="${spec#*:}"; pw="${rest%%:*}"; db="${rest#*:}"
      [ "$($P -c "select 1 from pg_roles where rolname='$u'")" = 1 ] || $P -c "create role $u login superuser password '$pw'"
      [ "$($P -c "select 1 from pg_database where datname='$db'")" = 1 ] || $P -c "create database $db owner $u"
    done
    echo "started. dev : postgresql+pg8000://asta:asta_dev_only@127.0.0.1:5640/asta"
    echo "        test: export ASTA_TEST_DATABASE_URL=postgresql+pg8000://asta_test:asta_test_only@127.0.0.1:5640/asta_test"
    ;;
  stop)
    "$BIN/pg_ctl" -D "$DATA" -m fast -w stop >/dev/null && echo stopped
    ;;
  status)
    "$BIN/pg_ctl" -D "$DATA" status 2>&1 | head -1
    ;;
  *) echo "usage: $0 start|stop|status" >&2; exit 2 ;;
esac
