#!/bin/sh
# ASTA backup-key relocation REHEARSAL with SYNTHETIC material only (M3-B-R3 workstream C).
#
#     sh backup-key-rehearsal.sh [report.json]
#
# Rehearses the procedure of docs/m3b/VAULT-AND-BACKUP.md s.3 end to end with a THROW-AWAY age key pair in a
# private temp directory: it never reads, creates, moves or deletes the real identity (~/asta-backup-identity)
# or the committed recipient, touches no network and no database. Steps proven:
#   1 generate a synthetic key pair (the "ceremony")          2 encrypt a synthetic dump on the "host" with the recipient only
#   3 relocate the identity to "offline media" and remove it from the "host"
#   4 the host can still back up but can NOT decrypt          5 restore from the offline identity, checksum equal
#   6 a different identity cannot decrypt                     7 the ciphertext holds no plaintext marker
#   8 a corrupted backup is detected (no silent bad restore)  9 cleanup (the temp directory is removed)
set -eu
command -v age >/dev/null 2>&1 && command -v age-keygen >/dev/null 2>&1 || { echo "age/age-keygen not installed" >&2; exit 2; }
REPORT="${1:-/dev/stdout}"
T="$(mktemp -d "${TMPDIR:-/tmp}/asta-keyrehearsal.XXXXXX")"
chmod 700 "$T"
trap 'rm -rf "$T"' EXIT INT TERM
HOST="$T/host"; OFFLINE="$T/offline-media"; mkdir -p "$HOST" "$OFFLINE"; chmod 700 "$HOST" "$OFFLINE"
OK=1; STEPS=""
step() { # name, result(0/1)
  if [ "$2" = 0 ]; then STEPS="$STEPS\"$1\":true,"; else STEPS="$STEPS\"$1\":false,"; OK=0; fi
}

age-keygen -o "$HOST/identity.txt" >/dev/null 2>&1; chmod 600 "$HOST/identity.txt"
age-keygen -y "$HOST/identity.txt" > "$HOST/recipient.pub"
step 1_generate_synthetic_pair 0

MARK="SYNTHETIC-DUMP-MARKER-$$"
{ echo "-- $MARK"; i=0; while [ $i -lt 2000 ]; do echo "INSERT INTO synthetic VALUES ($i, 'row-$i');"; i=$((i+1)); done; } > "$HOST/dump.sql"
SUM1="$(shasum -a 256 "$HOST/dump.sql" | cut -d' ' -f1)"
age -R "$HOST/recipient.pub" -o "$HOST/dump.sql.age" "$HOST/dump.sql"
rm -f "$HOST/dump.sql"
step 2_encrypt_with_recipient_only 0

mv "$HOST/identity.txt" "$OFFLINE/identity.txt"           # relocation: the identity leaves the host
[ ! -e "$HOST/identity.txt" ]; step 3_identity_removed_from_host $?

age -R "$HOST/recipient.pub" -o "$HOST/second.age" "$OFFLINE/identity.txt" 2>/dev/null; step 4a_host_can_still_encrypt $?
if age -d -i "$HOST/recipient.pub" -o /dev/null "$HOST/dump.sql.age" 2>/dev/null; then step 4b_host_cannot_decrypt 1; else step 4b_host_cannot_decrypt 0; fi

age -d -i "$OFFLINE/identity.txt" -o "$T/restored.sql" "$HOST/dump.sql.age"
SUM2="$(shasum -a 256 "$T/restored.sql" | cut -d' ' -f1)"
[ "$SUM1" = "$SUM2" ]; step 5_restore_from_offline_identity_checksum_equal $?

age-keygen -o "$T/other.txt" >/dev/null 2>&1
if age -d -i "$T/other.txt" -o /dev/null "$HOST/dump.sql.age" 2>/dev/null; then step 6_other_identity_cannot_decrypt 1; else step 6_other_identity_cannot_decrypt 0; fi

if grep -aq "$MARK" "$HOST/dump.sql.age"; then step 7_ciphertext_has_no_plaintext 1; else step 7_ciphertext_has_no_plaintext 0; fi

cp "$HOST/dump.sql.age" "$T/corrupt.age"
printf 'X' | dd of="$T/corrupt.age" bs=1 seek=400 conv=notrunc 2>/dev/null
if age -d -i "$OFFLINE/identity.txt" -o /dev/null "$T/corrupt.age" 2>/dev/null; then step 8_corruption_detected 1; else step 8_corruption_detected 0; fi

printf '{"rehearsal":"synthetic-only","real_identity_touched":false,%s"all_passed":%s}\n' "$STEPS" "$([ "$OK" = 1 ] && echo true || echo false)" > "$REPORT"
[ "$OK" = 1 ]
