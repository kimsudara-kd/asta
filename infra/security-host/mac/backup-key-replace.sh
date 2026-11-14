#!/bin/sh
# Replace the (exposed) backup age key pair - owner procedure helper (docs/m3b/VAULT-AND-BACKUP.md s.4).
#
#     sh backup-key-replace.sh --media /Volumes/KEYA [--recipient-out FILE]           # dry run (default)
#     sh backup-key-replace.sh --media /Volumes/KEYA [--recipient-out FILE] --apply --confirm-offline
#
# Generates a NEW age identity DIRECTLY ON OFFLINE MEDIA: the private key is never written to the Mac's internal
# disk. It refuses an internal volume, a path under your home, a read-only or unmounted target, and an identity
# file that already exists. It never reads, moves or deletes the old identity (~/asta-backup-identity): retiring
# that is a separate, deliberate step after the new key is proven. Only the PUBLIC recipient is written to
# --recipient-out (default ./backup-recipient.pub.new) for you to review and commit. No network, no sudo.
set -eu
APPLY=0; CONFIRM=0; MEDIA=""; OUT="./backup-recipient.pub.new"
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --confirm-offline) CONFIRM=1 ;;   # you state this media will be disconnected and stored away after the ceremony
    --media) MEDIA="${2:?path}"; shift ;;
    --recipient-out) OUT="${2:?file}"; shift ;;
    -h|--help) sed -n 2,13p "$0"; exit 0 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done
die() { printf 'REFUSING: %s\n' "$*" >&2; exit 1; }
[ -n "$MEDIA" ] || die "--media <mount point of the offline media> is required"
command -v age-keygen >/dev/null 2>&1 && command -v age >/dev/null 2>&1 || die "age/age-keygen not installed"
case "$MEDIA" in /Volumes/?*) ;; *) die "$MEDIA is not under /Volumes: the key must be generated on removable media" ;; esac
case "$MEDIA" in "$HOME"/*|/Users/*) die "$MEDIA is inside a home directory" ;; esac
[ -d "$MEDIA" ] || die "$MEDIA is not mounted"
[ -w "$MEDIA" ] || die "$MEDIA is not writable"
INFO="$(diskutil info "$MEDIA" 2>/dev/null)" || die "cannot inspect $MEDIA with diskutil"
printf '%s\n' "$INFO" | grep -Eq "Mounted: +Yes" || die "$MEDIA is not a mounted volume"
if printf '%s\n' "$INFO" | grep -Eq "Device Location: +Internal"; then
  die "$MEDIA is on an INTERNAL disk: the identity must live on offline external media only"
fi
case "$(printf '%s\n' "$INFO" | sed -n 's/.*File System Personality: *//p' | head -1)" in
  "") die "cannot determine the file system of $MEDIA" ;;
esac
ID="$MEDIA/asta-backup-identity.txt"
[ ! -e "$ID" ] || die "$ID already exists: refusing to overwrite a key"
[ ! -e "$OUT" ] || die "$OUT already exists: refusing to overwrite"
printf 'media: %s (external, writable)\nnew identity: %s\nrecipient (public) -> %s\n' "$MEDIA" "$ID" "$OUT"
echo "NOTE: a permanently attached drive is NOT offline media. --apply requires --confirm-offline: you state that this media is"
echo "      removed and stored away after the ceremony (and that a second copy will exist)."
if [ "$APPLY" != 1 ]; then echo "(dry-run: nothing was created)"; exit 0; fi
[ "$CONFIRM" = 1 ] || die "--apply needs --confirm-offline"

umask 077
age-keygen -o "$ID" >/dev/null 2>&1
chmod 600 "$ID"
age-keygen -y "$ID" > "$OUT"
T="$(mktemp -d "${TMPDIR:-/tmp}/asta-keyreplace.XXXXXX")"; trap 'rm -rf "$T"' EXIT INT TERM
printf 'asta synthetic round trip %s\n' "$$" > "$T/p"
age -R "$OUT" -o "$T/p.age" "$T/p"
age -d -i "$ID" -o "$T/p.out" "$T/p.age"
cmp -s "$T/p" "$T/p.out" || die "round trip failed: do not use this key"
echo "OK: new identity generated on the media, round trip verified, recipient written to $OUT"
echo "NEXT (owner): 1) copy the identity to a SECOND media and compare checksums; 2) review and commit $OUT as"
echo "      infra/security-host/mac/backup-recipient.pub; 3) restore-drill with the media; 4) only then retire the old"
echo "      pair (~/asta-backup-identity) and record the old recipient as revoked. Never put the identity in git, chat or cloud."
