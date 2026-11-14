#!/bin/sh
# ASTA security host - PRIVILEGED host provisioning for the Mac mini (design: docs/m3a/MAC-MINI-DEPLOYMENT-DESIGN.md s.10).
#
# DRY RUN BY DEFAULT: prints what it would do and changes nothing. Review the output, then run with sudo:
#     sudo sh provision-host.sh --apply [--vm-base DIR] [--socket-vmnet-tarball F --socket-vmnet-sha256 H] [--load] [--firewall]
#
# What --apply does (each step is idempotent):
#   1. service user/group `_astavm` (no login shell, hidden, no home)
#   2. /var/lib/asta-vm (+ run, images, vars, disks, seeds) 0700 _astavm; /var/log/asta; root-owned launcher dir.
#      --vm-base DIR puts the data on another volume (symlink /var/lib/asta-vm -> DIR). The volume must be APFS/HFS+
#      with OWNERSHIP ENABLED (file permissions are the VM-disk isolation): exFAT/NTFS/FAT and `noowners` mounts are
#      refused. For an external APFS/HFS+ volume: `sudo diskutil enableOwnership /Volumes/NAME` first.
#   3. socket_vmnet into /opt/socket_vmnet, ROOT-OWNED, only from a tarball whose SHA-256 you pass in
#      (never from a user-writable Homebrew path: that would let an unprivileged user run code as root)
#   4. the four LaunchDaemons (validated with plutil) - INSTALLED, loaded only with --load
#   5. macOS application firewall + stealth mode, only with --firewall
# What it deliberately does NOT do: FileVault, Wi-Fi/Bluetooth/sharing changes (they can cut your own remote
# session; decide and do them yourself), power settings (pmset autorestart is already 1), downloading anything,
# building/starting the VMs (that needs seeds + the Ubuntu image: see `qemu-vms.sh prepare`, Phase C).
set -eu

APPLY=0; LOAD=0; FIREWALL=0; TARBALL=""; SHA=""; VMBASE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --load) LOAD=1 ;;
    --firewall) FIREWALL=1 ;;
    --vm-base) VMBASE="${2:?dir}"; shift ;;
    --socket-vmnet-tarball) TARBALL="${2:?path}"; shift ;;
    --socket-vmnet-sha256) SHA="${2:?hex}"; shift ;;
    -h|--help) sed -n 2,23p "$0"; exit 0 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done

HERE="$(cd "$(dirname "$0")" && pwd)"
BASE=/var/lib/asta-vm      # fixed path the LaunchDaemons and qemu-vms.sh use
REAL="${VMBASE:-$BASE}"      # where the data really lives (a symlink at $BASE when --vm-base is used)
SVC=_astavm
LIBEXEC=/usr/local/libexec/asta
LOGDIR=/var/log/asta
DAEMONS="com.asta.socket_vmnet com.asta.vm.fw com.asta.vm.app com.asta.vm.cred"

say() { printf '%s\n' "$*"; }
run() { if [ "$APPLY" = 1 ]; then say "+ $*"; "$@"; else say "[dry-run] $*"; fi; }
die() { say "REFUSING: $*" >&2; exit 1; }

# ---------------------------------------------------------------- preflight (read-only, always runs)
say "== preflight"
[ "$(uname -sm)" = "Darwin arm64" ] || die "needs an Apple-silicon Mac (got $(uname -sm))"
[ "$(sysctl -n kern.hv_support 2>/dev/null)" = 1 ] || die "Hypervisor.framework unavailable"
if [ "$APPLY" = 1 ] && [ "$(id -u)" != 0 ]; then die "--apply needs root (sudo)"; fi
for f in qemu-vms.sh launchd/com.asta.socket_vmnet.plist launchd/com.asta.vm.fw.plist \
         launchd/com.asta.vm.app.plist launchd/com.asta.vm.cred.plist; do
  [ -f "$HERE/$f" ] || die "missing $f next to this script"
done
for d in $DAEMONS; do plutil -lint "$HERE/launchd/$d.plist" >/dev/null || die "invalid plist $d"; done
[ -x /opt/homebrew/bin/qemu-system-aarch64 ] || say "note: qemu-system-aarch64 not found in /opt/homebrew/bin (install QEMU via Homebrew first)"
AVAIL_GB=$(df -g / | awk 'NR==2{print $4}')
say "free space on /: ${AVAIL_GB} GB (design disks are sparse qcow2: fw 8 + app 60 + cred 10 + backup 20 GB maximum)"
[ "${AVAIL_GB:-0}" -ge 40 ] || say "WARNING: less than 40 GB free; consider ASTA_VM_BASE on another volume"
if [ -n "$VMBASE" ]; then
  case "$VMBASE" in /*) ;; *) die "--vm-base must be an absolute path" ;; esac
  P="$VMBASE"; while [ ! -e "$P" ]; do P="$(dirname "$P")"; done
  MP="$(df -P "$P" | awk 'NR==2{for(i=6;i<=NF;i++) printf "%s%s",$i,(i<NF?" ":"")}')"
  MLINE="$(mount | grep -F " on $MP (" | head -1)"
  [ -n "$MLINE" ] || die "cannot determine the mount of $VMBASE"
  FSTYPE="$(printf '%s' "$MLINE" | sed 's/.*(\([A-Za-z0-9]*\),.*/\1/')"
  case "$FSTYPE" in apfs|hfs) ;; *) die "$VMBASE is on a '$FSTYPE' volume ($MP): it cannot enforce file ownership/permissions, which are the VM-disk isolation. Use an APFS/HFS+ volume (age-encrypted BACKUPS may go on exFAT, VM disks may not)" ;; esac
  case "$MLINE" in *noowners*) die "$MP is mounted with ownership ignored (noowners). For an external APFS/HFS+ volume run: sudo diskutil enableOwnership $MP" ;; esac
  say "vm-base volume OK: $MP ($FSTYPE, ownership enforced)"
fi
if [ -n "$TARBALL" ] || [ -n "$SHA" ]; then
  [ -f "$TARBALL" ] || die "tarball not found: $TARBALL"
  [ -n "$SHA" ] || die "--socket-vmnet-sha256 is required with a tarball"
  GOT="$(shasum -a 256 "$TARBALL" | cut -d' ' -f1)"
  [ "$GOT" = "$SHA" ] || die "SHA-256 mismatch (expected $SHA, got $GOT)"
  say "socket_vmnet tarball checksum OK"
fi
say "mode: $([ "$APPLY" = 1 ] && echo APPLY || echo DRY-RUN)"

# ---------------------------------------------------------------- 1 service account
say; say "== 1 service account $SVC"
if dscl . -read "/Users/$SVC" UniqueID >/dev/null 2>&1; then
  say "exists: $SVC (skipping)"
else
  ID=""
  for c in $(seq 330 399); do
    dscl . -search /Users UniqueID "$c" | grep -q . || dscl . -search /Groups PrimaryGroupID "$c" | grep -q . || { ID=$c; break; }
  done
  [ -n "$ID" ] || die "no free id in 330-399"
  run dscl . -create "/Groups/$SVC"
  run dscl . -create "/Groups/$SVC" PrimaryGroupID "$ID"
  run dscl . -create "/Users/$SVC"
  run dscl . -create "/Users/$SVC" UniqueID "$ID"
  run dscl . -create "/Users/$SVC" PrimaryGroupID "$ID"
  run dscl . -create "/Users/$SVC" UserShell /usr/bin/false
  run dscl . -create "/Users/$SVC" NFSHomeDirectory /var/empty
  run dscl . -create "/Users/$SVC" RealName "ASTA VM service"
  run dscl . -create "/Users/$SVC" IsHidden 1
fi

# ---------------------------------------------------------------- 2 directories
say; say "== 2 directories"
run mkdir -p "$REAL" "$REAL/run" "$REAL/images" "$REAL/vars" "$REAL/disks" "$REAL/seeds" "$LOGDIR" "$LIBEXEC"
run chown -R "$SVC:$SVC" "$REAL"
run chmod 700 "$REAL" "$REAL/run"
if [ "$REAL" != "$BASE" ]; then
  if [ -L "$BASE" ] && [ "$(readlink "$BASE")" = "$REAL" ]; then say "symlink present: $BASE -> $REAL"
  elif [ -e "$BASE" ] || [ -L "$BASE" ]; then die "$BASE already exists and is not the expected symlink; resolve by hand"
  else run ln -s "$REAL" "$BASE"; fi
fi
run chown root:wheel "$LIBEXEC"
run chmod 755 "$LIBEXEC"
run chown "root:$SVC" "$LOGDIR"
run chmod 770 "$LOGDIR"    # only root (socket_vmnet) and $SVC (the VM daemons) can write logs here
run install -o root -g wheel -m 0755 "$HERE/qemu-vms.sh" "$LIBEXEC/qemu-vms.sh"   # launched as $SVC: must not be writable by it

# ---------------------------------------------------------------- 3 socket_vmnet (root-owned, checksum-pinned)
say; say "== 3 socket_vmnet"
if [ -x /opt/socket_vmnet/bin/socket_vmnet ]; then
  say "present: /opt/socket_vmnet/bin/socket_vmnet (skipping)"
elif [ -n "$TARBALL" ]; then
  run tar -xzf "$TARBALL" -C / opt/socket_vmnet
  run chown -R root:wheel /opt/socket_vmnet
  run chmod -R go-w /opt/socket_vmnet
else
  say "NOT installed: pass --socket-vmnet-tarball <release.tar.gz> --socket-vmnet-sha256 <hex>"
  say "  (release from https://github.com/lima-vm/socket_vmnet; verify the digest yourself; the uplink VM needs it)"
fi

# ---------------------------------------------------------------- 4 LaunchDaemons
say; say "== 4 LaunchDaemons (installed; loaded only with --load)"
for d in $DAEMONS; do
  run install -o root -g wheel -m 0644 "$HERE/launchd/$d.plist" "/Library/LaunchDaemons/$d.plist"
done
if [ "$LOAD" = 1 ]; then
  [ -x /opt/socket_vmnet/bin/socket_vmnet ] || die "--load needs socket_vmnet installed"
  [ -f "$BASE/disks/asta-fw.qcow2" ] || die "--load needs the prepared VM disks (qemu-vms.sh prepare, Phase C)"
  for d in $DAEMONS; do run launchctl bootstrap system "/Library/LaunchDaemons/$d.plist"; done
else
  say "not loading daemons (no VM disks/seeds yet). Later: --load, or launchctl bootstrap system <plist>"
fi

# ---------------------------------------------------------------- 5 firewall
say; say "== 5 macOS firewall"
if [ "$FIREWALL" = 1 ]; then
  run /usr/libexec/ApplicationFirewall/socketfilterfw --setglobalstate on
  run /usr/libexec/ApplicationFirewall/socketfilterfw --setstealthmode on
else
  say "skipped (pass --firewall). Note: it will prompt/deny inbound for the unrelated EXO.app listener."
fi

say; say "== not done by design: FileVault, Wi-Fi/Bluetooth/Sharing, power settings. Verify afterwards:"
say "   dscl . -read /Users/$SVC; ls -ld $BASE $LIBEXEC/qemu-vms.sh; ls -l /Library/LaunchDaemons/com.asta.*"
[ "$APPLY" = 1 ] || say "(dry-run: nothing was changed)"
