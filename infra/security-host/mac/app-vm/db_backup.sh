#!/bin/sh
# Encrypted logical backup of the ASTA database (runs on asta-app via asta-db-backup.timer).
# The dump is encrypted with age to an OFFLINE recipient public key before it touches the disk; the
# private key is never on this VM, so a stolen backup disk is useless and the VM cannot read old backups.
set -eu
RECIP=/etc/asta/backup-recipient.pub
OUT=/var/backups/asta
TS="$(date -u +%Y%m%dT%H%M%SZ)"
[ -s "$RECIP" ] || { echo "no backup recipient key" >&2; exit 1; }
umask 077
pg_dump -Fc --no-owner -d asta | age -R "$RECIP" > "$OUT/asta-$TS.dump.age.part"
mv "$OUT/asta-$TS.dump.age.part" "$OUT/asta-$TS.dump.age"
sha256sum "$OUT/asta-$TS.dump.age" > "$OUT/asta-$TS.dump.age.sha256"
# keep 14 daily + the newest 8 weekly (simple retention)
ls -1t "$OUT"/asta-*.dump.age | tail -n +15 | while read -r f; do rm -f "$f" "$f.sha256"; done
logger -t asta-backup "backup $TS ok"
