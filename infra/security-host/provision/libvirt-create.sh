#!/bin/sh
# Creates the ASTA security-host VMs with libvirt/KVM on a DEDICATED Linux host. DESIGN ARTIFACT (M3A-R3).
# DRY RUN BY DEFAULT. Usage: DRY_RUN=0 ./libvirt-create.sh <seed-dir> <ubuntu.iso> <host-uplink-nic>
set -eu
SEED="${1:?seed dir}"; ISO="${2:?iso}"; UPLINK="${3:?host uplink nic}"
DRY_RUN="${DRY_RUN:-1}"
run() { echo "> $*"; [ "$DRY_RUN" = "0" ] && "$@" || true; }

# isolated network: no forward mode = no NAT, no routing, no host bridge address
cat > /tmp/asta-prot.xml <<'EOF'
<network>
  <name>asta-prot</name>
  <bridge name="virbr-astaprot" stp="off"/>
</network>
EOF
run virsh net-define /tmp/asta-prot.xml
run virsh net-autostart asta-prot
run virsh net-start asta-prot

run virt-install --name asta-rt --vcpus 2 --memory 4096 --cpu host-passthrough --osinfo ubuntu24.04 \
    --disk size=30,format=qcow2 --disk "path=$SEED/runtime-vm.iso,device=cdrom" --cdrom "$ISO" \
    --network network=asta-prot,model=virtio --graphics none --sound none --noautoconsole --boot uefi,firmware.feature0.name=secure-boot,firmware.feature0.enabled=yes
run virt-install --name asta-fw --vcpus 1 --memory 1024 --cpu host-passthrough --osinfo ubuntu24.04 \
    --disk size=8,format=qcow2 --disk "path=$SEED/firewall-vm.iso,device=cdrom" --cdrom "$ISO" \
    --network network=asta-prot,model=virtio --network "type=direct,source=$UPLINK,source_mode=bridge,model=virtio" \
    --graphics none --sound none --noautoconsole --boot uefi,firmware.feature0.name=secure-boot,firmware.feature0.enabled=yes
run virsh snapshot-create-as asta-rt clean-before-install
run virsh snapshot-create-as asta-fw clean-before-install
echo "Done (DRY_RUN=$DRY_RUN)."
