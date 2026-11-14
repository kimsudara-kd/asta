#!/bin/sh
# Host-side copy of the app VM's backup image to an EXTERNAL drive (design s.6: "the host copies that image to
# the external drive during a maintenance window; the host cannot read ext4 and never sees plaintext").
# The image holds ONLY age-encrypted dumps (db_backup.sh); it is ciphertext, so the destination does not need
# to enforce file permissions (an exFAT drive is fine for THIS, unlike for VM disks).
#
# DRY RUN BY DEFAULT. Run with sudo (the image is 0700 _astavm):
#     sudo sh backup-image-to-external.sh --dest "/Volumes/<DRIVE>/asta-backups" [--keep 4] [--apply]
#
# Fail-closed rules (each refuses, nothing is written):
#   * --dest must be under /Volumes/<name>/ AND that volume must really be mounted (an unplugged drive would
#     otherwise make "mkdir -p" quietly create a folder on the internal disk and fill it)
#   * the image must not be open by any process (copying a live ext4 image gives an inconsistent backup):
#     stop the app VM first (launchctl bootout system/com.asta.vm.app), copy, then start it again
#   * the destination needs 1.2x the image size free
#   * the copy is written as *.part, its SHA-256 must equal the source's, only then is it renamed
#   * the script only ever creates files named asta-backup-<UTC>.img[.sha256] inside the --dest folder and only
#     prunes files with exactly that name pattern: it never touches anything else on the drive
set -eu

APPLY=0; DEST=""; KEEP=4
IMG="${ASTA_BACKUP_IMAGE:-/var/lib/asta-vm/disks/asta-backup.img}"
PREFIX="${ASTA_BACKUP_MOUNT_PREFIX:-/Volumes/}"   # test seam; the default is the only supported value
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --dest) DEST="${2:?dir}"; shift ;;
    --keep) KEEP="${2:?n}"; shift ;;
    -h|--help) sed -n 2,19p "$0"; exit 0 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done

say() { printf '%s\n' "$*"; }
die() { say "REFUSING: $*" >&2; exit 1; }
run() { if [ "$APPLY" = 1 ]; then say "+ $*"; "$@"; else say "[dry-run] $*"; fi; }

[ -n "$DEST" ] || die "--dest is required"
case "$DEST" in "$PREFIX"?*) ;; *) die "--dest must be inside a mounted external volume under $PREFIX (got $DEST)" ;; esac
case "$KEEP" in ''|*[!0-9]*) die "--keep must be a number" ;; esac
[ "$KEEP" -ge 1 ] || die "--keep must be >= 1"
[ -f "$IMG" ] || die "backup image not found: $IMG"
[ -r "$IMG" ] || die "cannot read $IMG (it is 0700 _astavm: run with sudo)"

# volume = first path component after the prefix; it must be a real mount (fail closed when unplugged)
REST="${DEST#"$PREFIX"}"; VOL="$PREFIX${REST%%/*}"
if [ "$PREFIX" = "/Volumes/" ]; then
  mount | grep -qF " on $VOL (" || die "volume $VOL is not mounted (drive unplugged?)"
else
  [ -d "$VOL" ] || die "volume $VOL is missing"
fi

# consistency: nobody may have the image open
if command -v lsof >/dev/null 2>&1 && lsof "$IMG" >/dev/null 2>&1; then
  die "$IMG is open (the app VM is running). Stop it for the maintenance window: launchctl bootout system/com.asta.vm.app"
fi

SIZE="$(stat -f%z "$IMG")"
AVAIL_KB="$(df -Pk "$VOL" | awk 'NR==2{print $4}')"
NEED_KB=$(( SIZE / 1024 * 12 / 10 + 1 ))
[ "$AVAIL_KB" -ge "$NEED_KB" ] || die "not enough free space on $VOL (need ${NEED_KB} KB, have ${AVAIL_KB} KB)"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$DEST/asta-backup-$TS.img"
say "image : $IMG ($SIZE bytes)"
say "target: $OUT (keep $KEEP)"
say "mode  : $([ "$APPLY" = 1 ] && echo APPLY || echo DRY-RUN)"

run mkdir -p "$DEST"
run cp "$IMG" "$OUT.part"
if [ "$APPLY" = 1 ]; then
  SRC_SUM="$(shasum -a 256 "$IMG" | cut -d' ' -f1)"
  DST_SUM="$(shasum -a 256 "$OUT.part" | cut -d' ' -f1)"
  if [ "$SRC_SUM" != "$DST_SUM" ]; then
    rm -f "$OUT.part"
    die "copy verification FAILED (source $SRC_SUM, copy $DST_SUM); partial file removed"
  fi
  mv "$OUT.part" "$OUT"
  printf '%s  %s\n' "$SRC_SUM" "asta-backup-$TS.img" > "$OUT.sha256"
  say "verified copy: $SRC_SUM"
  # retention: only exactly-named files of this script, newest KEEP stay
  ls -1t "$DEST"/asta-backup-[0-9]*T[0-9]*Z.img 2>/dev/null | tail -n +"$((KEEP + 1))" | while read -r old; do
    say "+ prune $old"; rm -f "$old" "$old.sha256"
  done
else
  say "[dry-run] sha256 verify, rename, write .sha256, prune to newest $KEEP"
  say "(dry-run: nothing was changed)"
fi
say "Remember: this copy is ciphertext. The age PRIVATE key must be stored OFFLINE and separately (not on this drive)."
