#!/bin/sh
# Restore drill: decrypt a backup (needs the OFFLINE private key, supplied by the operator at the
# console) into a SCRATCH database and verify it with the ASTA integrity tooling. Never touches the
# live database. Usage: db_restore_verify.sh <backup.dump.age> <age-identity-file>
set -eu
BK="${1:?backup file}"; ID="${2:?age identity file}"
SCR="asta_restore_check"
sha256sum -c "$BK.sha256"
dropdb --if-exists "$SCR"; createdb "$SCR"
age -d -i "$ID" "$BK" | pg_restore --no-owner --exit-on-error -d "$SCR"
ASTA_DATABASE_URL="postgresql+pg8000://asta@/$SCR?unix_sock=/var/run/postgresql/.s.PGSQL.5432" \
  /opt/asta/venv/bin/python /opt/asta/src/scripts/paper_integrity.py > "/tmp/restore-check.json"
cat /tmp/restore-check.json
dropdb "$SCR"
echo "restore drill finished: compare ids/config hashes/reconciliation with the source"
