#!/bin/sh
# Reverses install-no-vm.sh. DRY RUN BY DEFAULT.  sudo sh uninstall-no-vm.sh --apply [--purge-data]
# Keeps the state directory (replay/spend databases) unless --purge-data; never touches anything outside the paths below.
set -eu
APPLY=0; PURGE=0
for a in "$@"; do case "$a" in --apply) APPLY=1;; --purge-data) PURGE=1;; *) echo "REFUSING: unknown option $a" >&2; exit 2;; esac; done
CODE=/opt/asta-cred; ETC=/etc/asta-cred; VAR=/var/asta-cred; RUN=/var/asta-cred-run; LOG=/var/log/asta-cred; PLIST=/Library/LaunchDaemons/com.asta.cred.plist
say() { printf '%s\n' "$*"; }
run() { if [ "$APPLY" = 1 ]; then say "+ $*"; "$@"; else say "[dry-run] $*"; fi; }
[ "$(uname -s)" = Darwin ] || { echo "REFUSING: macOS only" >&2; exit 1; }
if [ "$APPLY" = 1 ] && [ "$(id -u)" != 0 ]; then echo "REFUSING: --apply needs root (sudo); nothing was changed" >&2; exit 1; fi
say "== plan (APPLY=$APPLY PURGE=$PURGE)"
[ -f "$PLIST" ] && run launchctl bootout system "$PLIST" || true
[ -f "$PLIST" ] && run rm -f "$PLIST"
run rm -rf "$CODE"
run rm -rf "$LOG"
run rm -rf "$RUN"
run rm -f "$ETC/cred.json"
say "(kept: $ETC/auth.key and $VAR unless --purge-data)"
if [ "$PURGE" = 1 ]; then run rm -rf "$ETC" "$VAR"; run dscl . -delete /Users/asta-cred; run dscl . -delete /Groups/asta-ipc; fi
if [ "$APPLY" = 1 ]; then say "== done"; else say "== dry run: nothing was changed"; fi
