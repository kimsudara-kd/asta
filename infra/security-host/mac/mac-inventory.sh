#!/bin/sh
# READ-ONLY inventory of the Mac mini (ASTA-M3A-MAC-01, Phase A). Run it ON THE MAC as the normal user:
#     sh mac-inventory.sh > mac-inventory.txt
# It installs nothing, changes no setting, uses no sudo and writes only to stdout. Commands that need
# administrator rights simply report that they could not be read; they are never retried with sudo.
# Send the output file back for the Phase A report (it contains hardware/OS facts, no secrets).
set -u
sec() { printf '\n=== %s ===\n' "$1"; }
run() { printf '$ %s\n' "$*"; "$@" 2>&1 | head -${LINES_MAX:-60}; }

sec "1 model, chip, macOS"
run sw_vers
run uname -mrs
run sysctl -n hw.model machdep.cpu.brand_string hw.ncpu hw.memsize
run sysctl -n hw.optional.arm64 kern.hv_support
run system_profiler SPHardwareDataType

sec "2 RAM and storage"
run vm_stat
run df -H /
run diskutil list internal
run diskutil apfs list

sec "3 disk encryption and platform security (read only)"
run fdesetup status
run csrutil status
run spctl --status
run /usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate
run /usr/libexec/ApplicationFirewall/socketfilterfw --getstealthmode

sec "4 network adapters and router facing facts"
run networksetup -listallhardwareports
run networksetup -listnetworkserviceorder
run ifconfig -a
run netstat -rn -f inet
run scutil --dns
run system_profiler SPNetworkDataType

sec "5 virtualization and tooling already installed (presence only)"
for t in qemu-system-aarch64 qemu-img limactl lima tart utm UTM prlctl vmrun VBoxManage docker brew python3 uv git xorriso mkisofs hdiutil socket_vmnet age pg_dump; do
  p="$(command -v "$t" 2>/dev/null || true)"; printf '%-22s %s\n' "$t" "${p:-not found}"
done
run python3 --version
run brew --version
ls /Applications 2>/dev/null | head -80
run sysctl -n kern.hv_support
run sysctl -n kern.hv_vmm_present

sec "6 backup availability (read only)"
run tmutil destinationinfo
run tmutil latestbackup
run diskutil list external

sec "7 power and remote management (read only)"
run pmset -g
run pmset -g custom
run systemsetup -getremotelogin
run systemsetup -getcomputersleep
run systemsetup -getwakeonnetworkaccess
run systemsetup -getrestartpowerfailure
run launchctl print-disabled system
run who

sec "8 listening sockets and sharing services (read only)"
run netstat -an -p tcp
run lsof -nP -iTCP -sTCP:LISTEN

sec "9 time"
run date -u
run sntp -d time.apple.com

echo; echo "=== end of read-only inventory (nothing was changed) ==="
