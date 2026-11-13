# Creates the ASTA security-host VMs on a DEDICATED Hyper-V host (Windows Pro/Enterprise/Server).
# DESIGN ARTIFACT (M3A-R3). DRY RUN BY DEFAULT: prints the commands, executes nothing.
# DO NOT RUN ON THE DEVELOPMENT PC (Windows 11 Home; installing/enabling a hypervisor there is not authorized).
#   .\hyperv-create.ps1 -SeedDir D:\asta-seed -IsoPath D:\ubuntu-24.04-live-server-amd64.iso            # dry run
#   .\hyperv-create.ps1 -SeedDir D:\asta-seed -IsoPath ... -UplinkAdapter "Ethernet" -Execute           # real
param(
  [Parameter(Mandatory)][string]$SeedDir,
  [Parameter(Mandatory)][string]$IsoPath,
  [string]$UplinkAdapter = "Ethernet",
  [string]$Root = "D:\ASTA-VMs",
  [switch]$Execute
)
$ErrorActionPreference = "Stop"
function Run($cmd) { Write-Host "> $cmd"; if ($Execute) { Invoke-Expression $cmd } }

# private switch with NO uplink and NO host adapter: the runtime VM can only talk to the firewall VM
Run "New-VMSwitch -Name 'asta-prot' -SwitchType Private"
# uplink switch bound to a physical NIC, NOT shared with the management OS
Run "New-VMSwitch -Name 'asta-up' -NetAdapterName '$UplinkAdapter' -AllowManagementOS `$false"

foreach ($vm in @(
  @{ Name = 'asta-fw'; Cpu = 1; Ram = 1GB;  Disk = 8GB;  Switches = @('asta-prot','asta-up'); Seed = 'firewall-vm' },
  @{ Name = 'asta-rt'; Cpu = 2; Ram = 4GB;  Disk = 30GB; Switches = @('asta-prot');            Seed = 'runtime-vm'  }
)) {
  $dir = Join-Path $Root $vm.Name
  Run "New-Item -ItemType Directory -Force -Path '$dir' | Out-Null"
  Run "New-VM -Name '$($vm.Name)' -Generation 2 -MemoryStartupBytes $($vm.Ram) -NewVHDPath '$dir\disk.vhdx' -NewVHDSizeBytes $($vm.Disk) -SwitchName '$($vm.Switches[0])'"
  if ($vm.Switches.Count -gt 1) { Run "Add-VMNetworkAdapter -VMName '$($vm.Name)' -SwitchName '$($vm.Switches[1])'" }
  Run "Set-VMProcessor -VMName '$($vm.Name)' -Count $($vm.Cpu) -ExposeVirtualizationExtensions `$false"
  Run "Set-VMMemory -VMName '$($vm.Name)' -DynamicMemoryEnabled `$false"
  Run "Set-VMFirmware -VMName '$($vm.Name)' -EnableSecureBoot On -SecureBootTemplate MicrosoftUEFICertificateAuthority"
  Run "Add-VMDvdDrive -VMName '$($vm.Name)' -Path '$IsoPath'"
  Run "Add-VMDvdDrive -VMName '$($vm.Name)' -Path '$SeedDir\$($vm.Seed).iso'"   # cloud-init NoCloud seed (label cidata)
  # no integration channels that bridge to the host
  foreach ($svc in 'Guest Service Interface','Heartbeat') { }
  Run "Disable-VMIntegrationService -VMName '$($vm.Name)' -Name 'Guest Service Interface'"
  Run "Set-VM -Name '$($vm.Name)' -AutomaticCheckpointsEnabled `$false -CheckpointType Standard"
  # MAC/IP spoofing guards on the private switch
  Run "Set-VMNetworkAdapter -VMName '$($vm.Name)' -MacAddressSpoofing Off -RouterGuard On -DhcpGuard On"
  Run "Checkpoint-VM -Name '$($vm.Name)' -SnapshotName 'clean-before-install'"
}
Write-Host "Done (dry run = nothing executed). Next: install Ubuntu from the ISO, then run acceptance (docs/m3a/VM-BOUNDARY-DESIGN.md)."
