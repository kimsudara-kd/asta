#!/bin/sh
# Creates the ASTA security-host VMs with VirtualBox on a DEDICATED host. DESIGN ARTIFACT (M3A-R3).
# DRY RUN BY DEFAULT (prints commands). DO NOT RUN ON THE DEVELOPMENT PC (installing a hypervisor there
# is not authorized). Usage: DRY_RUN=0 ./virtualbox-create.sh <seed-dir> <ubuntu.iso> <host-uplink-nic>
set -eu
SEED="${1:?seed dir}"; ISO="${2:?iso}"; UPLINK="${3:?host uplink nic}"
DRY_RUN="${DRY_RUN:-1}"
run() { echo "> $*"; [ "$DRY_RUN" = "0" ] && "$@" || true; }

# 'asta-prot' is an INTERNAL network: VirtualBox provides no host adapter and no uplink on it
create() {  # name cpus ram_mb disk_mb nic1 nic2
  NAME="$1"; run VBoxManage createvm --name "$NAME" --ostype Ubuntu_64 --register
  run VBoxManage modifyvm "$NAME" --cpus "$2" --memory "$3" --firmware efi --nested-hw-virt off \
      --clipboard-mode disabled --draganddrop disabled --audio-enabled off --usb-ohci off --usb-ehci off --usb-xhci off \
      --vrde off --paravirtprovider kvm --graphicscontroller vmsvga --rtcuseutc on
  run VBoxManage createmedium disk --filename "$NAME.vdi" --size "$4" --variant Standard
  run VBoxManage storagectl "$NAME" --name SATA --add sata --controller IntelAhci
  run VBoxManage storageattach "$NAME" --storagectl SATA --port 0 --device 0 --type hdd --medium "$NAME.vdi"
  run VBoxManage storageattach "$NAME" --storagectl SATA --port 1 --device 0 --type dvddrive --medium "$ISO"
  run VBoxManage storageattach "$NAME" --storagectl SATA --port 2 --device 0 --type dvddrive --medium "$SEED/$5.iso"
  run VBoxManage modifyvm "$NAME" --nic1 intnet --intnet1 asta-prot --nicpromisc1 deny
}
create asta-rt 2 4096 30720 runtime-vm
create asta-fw 1 1024 8192 firewall-vm
# the firewall VM's second NIC is the ONLY uplink (bridged to a dedicated host NIC)
run VBoxManage modifyvm asta-fw --nic2 bridged --bridgeadapter2 "$UPLINK" --nicpromisc2 deny
run VBoxManage snapshot asta-rt take clean-before-install
run VBoxManage snapshot asta-fw take clean-before-install
echo "Done (DRY_RUN=$DRY_RUN)."
