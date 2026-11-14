#!/bin/sh
# ASTA security host - ROLLBACK of provision-host.sh (design: docs/m3b/MAC-READINESS-AUDIT.md s.6).
#
# DRY RUN BY DEFAULT: prints what it would do and changes nothing. With sudo:
#     sudo sh rollback-host.sh --apply [--purge-data] [--purge-socket-vmnet] [--remove-account] [--restore-firewall]
#
# Default (--apply alone) is the SAFE rollback: stop and unload the four LaunchDaemons, remove their plists and
# the root-owned launcher. It KEEPS the VM disks, the encrypted backups, the logs, the service account, socket_vmnet
# and the firewall setting, so nothing of evidential or recovery value is destroyed by accident.
#   --purge-data           also delete /var/lib/asta-vm (and what --vm-base pointed to): VM disks, seeds, backups
#   --purge-socket-vmnet   also delete /opt/socket_vmnet
#   --remove-account       also delete the _astavm user and group (only after the data is gone or re-owned)
#   --restore-firewall     set the macOS application firewall and stealth mode OFF (the state before provisioning)
# It never touches FileVault, Wi-Fi/Bluetooth/sharing, power settings, Docker, Homebrew or any other project.
set -eu

APPLY=0; PURGE=0; SOCK=0; ACCT=0; FW=0
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --purge-data) PURGE=1 ;;
    --purge-socket-vmnet) SOCK=1 ;;
    --remove-account) ACCT=1 ;;
    --restore-firewall) FW=1 ;;
    -h|--help) sed -n 2,16p "$0"; exit 0 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done

BASE=/var/lib/asta-vm
SVC=_astavm
LIBEXEC=/usr/local/libexec/asta
DAEMONS="com.asta.vm.cred com.asta.vm.app com.asta.vm.fw com.asta.socket_vmnet"   # stop the VMs before their uplink

say() { printf '%s\n' "$*"; }
run() { if [ "$APPLY" = 1 ]; then say "+ $*"; "$@"; else say "[dry-run] $*"; fi; }
die() { say "REFUSING: $*" >&2; exit 1; }

say "== preflight"
[ "$(uname -s)" = Darwin ] || die "macOS only"
if [ "$APPLY" = 1 ] && [ "$(id -u)" != 0 ]; then die "--apply needs root (sudo)"; fi
say "mode: $([ "$APPLY" = 1 ] && echo APPLY || echo DRY-RUN)"
if [ "$ACCT" = 1 ] && [ "$PURGE" = 0 ] && [ -e "$BASE" ]; then
  die "--remove-account with data still present would orphan files owned by $SVC; add --purge-data (or move the data first)"
fi
case "$(readlink "$BASE" 2>/dev/null || echo "$BASE")" in
  /|/Users|/Users/*|/System*|/Library*|/Applications*|/usr*|/etc*|/private*|/opt*|/bin|/sbin|/var|/var/db*|/Volumes|/Volumes/*/)
    case "$(readlink "$BASE" 2>/dev/null || echo "$BASE")" in /Users/Shared/?*) ;; *) die "$BASE points at a protected location; refusing to delete anything there" ;; esac ;;
esac

say; say "== 1 stop and unload the daemons (VMs first, then their uplink)"
for d in $DAEMONS; do
  if launchctl print "system/$d" >/dev/null 2>&1; then run launchctl bootout "system/$d"; else say "not loaded: $d"; fi
done

say; say "== 2 remove the LaunchDaemon plists and the root-owned launcher"
for d in $DAEMONS; do
  if [ -f "/Library/LaunchDaemons/$d.plist" ]; then run rm -f "/Library/LaunchDaemons/$d.plist"; else say "absent: $d.plist"; fi
done
if [ -f "$LIBEXEC/qemu-vms.sh" ]; then run rm -f "$LIBEXEC/qemu-vms.sh"; run rmdir "$LIBEXEC"; else say "absent: $LIBEXEC/qemu-vms.sh"; fi

say; say "== 3 data (kept unless --purge-data)"
if [ "$PURGE" = 1 ]; then
  if [ -L "$BASE" ]; then T="$(readlink "$BASE")"; run rm -f "$BASE"; run rm -rf "$T"
  elif [ -d "$BASE" ]; then run rm -rf "$BASE"
  else say "absent: $BASE"; fi
else
  say "kept: $BASE (VM disks, seeds, encrypted backups). Copy the encrypted backup image off the host first if you need it."
fi

say; say "== 4 socket_vmnet (kept unless --purge-socket-vmnet)"
if [ "$SOCK" = 1 ]; then run rm -rf /opt/socket_vmnet; else say "kept: /opt/socket_vmnet"; fi

say; say "== 5 service account (kept unless --remove-account)"
if [ "$ACCT" = 1 ]; then
  if dscl . -read "/Users/$SVC" UniqueID >/dev/null 2>&1; then run dscl . -delete "/Users/$SVC"; run dscl . -delete "/Groups/$SVC"; else say "absent: $SVC"; fi
else
  say "kept: $SVC"
fi

say; say "== 6 firewall (left as is unless --restore-firewall)"
if [ "$FW" = 1 ]; then
  run /usr/libexec/ApplicationFirewall/socketfilterfw --setstealthmode off
  run /usr/libexec/ApplicationFirewall/socketfilterfw --setglobalstate off
else
  say "left as is"
fi

say; say "== kept always: /var/log/asta (evidence), FileVault, Wi-Fi/Bluetooth/sharing, power settings, Docker, Homebrew, all other projects"
say "   verify afterwards: ls -l /Library/LaunchDaemons/com.asta.* ; ls -ld $BASE ; dscl . -read /Users/$SVC"
[ "$APPLY" = 1 ] || say "(dry-run: nothing was changed)"
