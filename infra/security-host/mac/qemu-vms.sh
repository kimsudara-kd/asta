#!/bin/sh
# ASTA security host on an Apple Silicon Mac mini: three Linux ARM64 VMs with QEMU + Hypervisor.framework.
# DESIGN ARTIFACT (M3A-MAC-01). DRY RUN BY DEFAULT: prints commands. Nothing here is executed on the
# development PC; on the Mac it needs a separate provisioning authorization (Phase C).
#
#   ./qemu-vms.sh prepare             # create disks, firmware vars, cloud-init seed ISOs   (DRY_RUN=0 to execute)
#   ./qemu-vms.sh run fw|app|cred     # run one VM in the foreground (launchd calls this)     (DRY_RUN=0 to execute)
#   ./qemu-vms.sh describe            # print the topology
#
# Why QEMU+HVF: it is open source, scriptable, runs ARM64 guests natively on Apple Silicon (hvf), and its
# `stream` netdev builds point-to-point L2 links over UNIX sockets in a 0700 directory: the links exist
# nowhere on the host network (no vmnet interface, no host IP, no bridge), so the app and credential VMs
# cannot reach macOS or each other except over the links defined here. Apple Virtualization.framework
# front ends (Tart, Lima vz, UTM-AVF) offer only NAT/bridged/host-only vmnet networking, which gives the
# host an address on the guest segment; they are therefore not used for the isolated links.
set -eu
DRY_RUN="${DRY_RUN:-1}"
BASE="${ASTA_VM_BASE:-/var/lib/asta-vm}"          # owned by the unprivileged user _astavm, mode 0700
RUN="$BASE/run"                                   # unix sockets (links, consoles), mode 0700
SEEDS="${ASTA_SEED_DIR:-$BASE/seeds}"             # output of build_seed.py --target mac
FW_CODE="${QEMU_EFI_CODE:-/opt/homebrew/share/qemu/edk2-aarch64-code.fd}"
FW_VARS_TEMPLATE="${QEMU_EFI_VARS:-/opt/homebrew/share/qemu/edk2-arm-vars.fd}"
UPLINK_SOCK="${ASTA_UPLINK_SOCK:-/var/run/socket_vmnet.bridged}"   # root-owned socket_vmnet bridged to the Mac's Ethernet
UBUNTU_IMG="${UBUNTU_IMG:-$BASE/images/ubuntu-24.04-server-cloudimg-arm64.img}"

run() { echo "> $*"; [ "$DRY_RUN" = "0" ] && "$@" || true; }

# name vcpus ram_mb disk_gb
spec() {
  case "$1" in
    fw)   echo "1 1024 8" ;;
    app)  echo "3 5120 60" ;;     # paper service, API, PostgreSQL, backups
    cred) echo "1 2048 10" ;;     # vault + connector only (M3-B services not installed yet)
    *) echo "unknown vm $1" >&2; exit 2 ;;
  esac
}

# point-to-point links: server side listens, client side connects (unix sockets, no host network)
# L1 fw<->app, L2 fw<->cred. L3 app<->cred is NOT attached until M3-B is authorized.
nics() {
  case "$1" in
    fw)   echo "-netdev stream,id=app0,addr.type=unix,addr.path=$RUN/l1.sock,server=on,reconnect-ms=2000 -device virtio-net-pci,netdev=app0,mac=52:54:00:77:01:01 \
                -netdev stream,id=cred0,addr.type=unix,addr.path=$RUN/l2.sock,server=on,reconnect-ms=2000 -device virtio-net-pci,netdev=cred0,mac=52:54:00:77:02:01 \
                -netdev stream,id=up0,addr.type=unix,addr.path=$UPLINK_SOCK -device virtio-net-pci,netdev=up0,mac=52:54:00:77:00:01" ;;
    app)  echo "-netdev stream,id=fw0,addr.type=unix,addr.path=$RUN/l1.sock,server=off,reconnect-ms=2000 -device virtio-net-pci,netdev=fw0,mac=52:54:00:77:01:02" ;;
    cred) echo "-netdev stream,id=fw0,addr.type=unix,addr.path=$RUN/l2.sock,server=off,reconnect-ms=2000 -device virtio-net-pci,netdev=fw0,mac=52:54:00:77:02:02" ;;
  esac
}

case "${1:-describe}" in
  describe)
    cat <<'EOT'
asta-fw   10.77.1.1 (app0) 10.77.2.1 (cred0) up0 = bridged uplink   1 vCPU  1 GB   8 GB   nftables + proxy, never forwards
asta-app  10.77.1.2 (fw0)                                           3 vCPU  5 GB  60 GB   paper service, API, PostgreSQL (unix socket), backups; NO credentials
asta-cred 10.77.2.2 (fw0)                                           1 vCPU  2 GB  10 GB   vault + connector only (future)
No host file sharing, no USB, no clipboard, no host-only/NAT/vmnet adapters; consoles are unix sockets in a 0700 directory.
EOT
    ;;
  prepare)
    run mkdir -p "$RUN" "$SEEDS" "$BASE/images" "$BASE/vars" "$BASE/disks"
    run chmod 700 "$BASE" "$RUN"
    for vm in fw app cred; do
      set -- $(spec "$vm")
      run qemu-img create -f qcow2 -F qcow2 -b "$UBUNTU_IMG" "$BASE/disks/asta-$vm.qcow2" "${3}G"
      run cp "$FW_VARS_TEMPLATE" "$BASE/vars/asta-$vm.vars.fd"
      run hdiutil makehybrid -iso -joliet -default-volume-name cidata -o "$SEEDS/asta-$vm.iso" "$SEEDS/$vm"
    done
    # encrypted-backup disk of the app VM: holds only age-encrypted dumps; the host copies this image to the
    # external backup drive during a maintenance window (the host cannot read ext4 and never sees plaintext)
    run qemu-img create -f raw "$BASE/disks/asta-backup.img" 20G
    ;;
  run)
    vm="${2:?fw|app|cred}"
    set -- $(spec "$vm")
    cpus="$1"; ram="$2"
    # shellcheck disable=SC2046
    run qemu-system-aarch64 -name "asta-$vm" -machine virt,gic-version=3 -accel hvf -cpu host -smp "$cpus" -m "$ram" \
      -display none -monitor none -parallel none -nodefaults \
      -chardev "socket,id=con,path=$RUN/$vm.console,server=on,wait=off" -serial chardev:con \
      -drive "if=pflash,format=raw,readonly=on,file=$FW_CODE" \
      -drive "if=pflash,format=raw,file=$BASE/vars/asta-$vm.vars.fd" \
      -drive "if=virtio,format=qcow2,file=$BASE/disks/asta-$vm.qcow2" \
      -drive "if=virtio,format=raw,readonly=on,file=$SEEDS/asta-$vm.iso" \
      -device virtio-rng-pci \
      $(nics "$vm")
    ;;
  *) echo "usage: $0 describe|prepare|run <vm>" >&2; exit 2 ;;
esac
