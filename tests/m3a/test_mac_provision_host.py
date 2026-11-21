"""Privileged host provisioning script (security-host/mac/provision-host.sh): safety properties."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

MAC = Path(__file__).resolve().parents[2] / "infra" / "security-host" / "mac"
SCRIPT = MAC / "provision-host.sh"
TEXT = SCRIPT.read_text(encoding="utf-8")
CODE = "\n".join(ln for ln in TEXT.splitlines() if not ln.lstrip().startswith("#"))


def test_it_never_downloads_installs_packages_or_touches_the_owners_remote_access():
    for banned in (
        "curl",
        "wget",
        "brew",
        "pip",
        "fdesetup",
        "networksetup",
        "pmset",
        "systemsetup",
    ):
        assert not re.search(rf"(?<![\w/.-]){banned}\s", CODE), banned  # as a command word
    # firewall change only behind an explicit flag; daemons loaded only behind --load
    assert "--firewall" in CODE and "FIREWALL" in CODE and "LOAD" in CODE


def test_dry_run_is_the_default_and_apply_needs_root():
    assert "APPLY=0" in CODE
    assert re.search(r'\[ "\$APPLY" = 1 \] && \[ "\$\(id -u\)" != 0 \]', CODE)
    assert "[dry-run]" in CODE


def test_socket_vmnet_comes_only_from_a_checksum_pinned_tarball_and_ends_root_owned():
    assert "shasum -a 256" in CODE and "SHA-256 mismatch" in CODE
    assert (
        "chown -R root:wheel /opt/socket_vmnet" in CODE
        and "chmod -R go-w /opt/socket_vmnet" in CODE
    )


def test_nothing_is_world_writable_and_the_service_cannot_modify_its_own_launcher():
    for bad in ("777", "1777", "666"):
        assert bad not in CODE, bad
    # the launcher run by the _astavm daemons is installed root-owned, not writable by that user
    assert "install -o root -g wheel -m 0755" in CODE and "qemu-vms.sh" in CODE
    assert 'chmod 700 "$REAL" "$REAL/run"' in CODE


def test_launch_daemons_reference_exactly_what_the_script_installs():
    plists = {p.stem: p.read_text(encoding="utf-8") for p in (MAC / "launchd").glob("*.plist")}
    assert set(plists) == {
        "com.asta.socket_vmnet",
        "com.asta.vm.fw",
        "com.asta.vm.app",
        "com.asta.vm.cred",
    }
    for name in plists:
        assert name in TEXT
    assert "/usr/local/libexec/asta/qemu-vms.sh" in plists["com.asta.vm.fw"]
    assert "/opt/socket_vmnet/bin/socket_vmnet" in plists["com.asta.socket_vmnet"]
    assert "<string>root</string>" in plists["com.asta.socket_vmnet"]
    for vm in ("fw", "app", "cred"):
        assert "<string>_astavm</string>" in plists[f"com.asta.vm.{vm}"]  # VMs never run as root


@pytest.mark.skipif(sys.platform != "darwin", reason="uses dscl/plutil/sysctl")
def test_default_run_on_a_mac_changes_nothing_and_refuses_apply_without_root():
    out = subprocess.run(  # noqa: S603  (fixed argv: repo-owned script, dry-run)
        ["/bin/sh", str(SCRIPT)], capture_output=True, text=True, timeout=60, check=False
    )
    assert out.returncode == 0 and "DRY-RUN" in out.stdout and "nothing was changed" in out.stdout
    assert not any(ln.startswith("+ ") for ln in out.stdout.splitlines())  # nothing executed
    if os.geteuid() != 0:
        ap = subprocess.run(  # noqa: S603  (refused before any change: not root)
            ["/bin/sh", str(SCRIPT), "--apply"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert ap.returncode != 0 and "needs root" in ap.stderr


def test_vm_base_must_be_on_a_volume_that_enforces_ownership():
    # VM-disk isolation is file permissions: exFAT/NTFS/FAT and noowners mounts are refused
    assert "--vm-base" in CODE and 'case "$FSTYPE" in apfs|hfs' in CODE
    assert "noowners" in CODE and "diskutil enableOwnership" in TEXT
    assert "cannot enforce file ownership" in CODE
    # the fixed path the LaunchDaemons use stays a symlink to the real location, never a copy
    assert 'ln -s "$REAL" "$BASE"' in CODE and "is not the expected symlink" in CODE


@pytest.mark.skipif(sys.platform != "darwin", reason="uses mount/df")
def test_vm_base_on_the_internal_apfs_volume_is_accepted_in_a_dry_run():
    out = subprocess.run(  # noqa: S603  (fixed argv: repo-owned script, dry-run)
        ["/bin/sh", str(SCRIPT), "--vm-base", "/Users/Shared/asta-vm-dryrun"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert "ownership enforced" in out.stdout and "nothing was changed" in out.stdout


def test_the_service_account_gets_no_usable_password():
    assert "Password '*'" in CODE and "UserShell /usr/bin/false" in CODE and "IsHidden 1" in CODE


def test_vm_base_refuses_protected_locations_and_foreign_non_empty_directories():
    for bad in (
        "/",
        "/Users/apple",
        "/Library/x",
        "/etc/asta",
        "/private/var/x",
        "/Applications/y",
    ):
        r = subprocess.run(["sh", str(SCRIPT), "--vm-base", bad], capture_output=True, text=True)
        assert r.returncode == 1 and "REFUSING" in (r.stdout + r.stderr), bad
    if (
        sys.platform != "darwin"
    ):  # needs the macOS /Users/Shared volume; the refusals above already ran
        return
    import shutil
    import uuid

    foreign = Path("/Users/Shared") / f"asta-test-{uuid.uuid4().hex[:8]}"
    foreign.mkdir()
    try:
        (foreign / "precious.txt").write_text("do not chown me")
        r = subprocess.run(
            ["sh", str(SCRIPT), "--vm-base", str(foreign)], capture_output=True, text=True
        )
        assert r.returncode == 1 and "refusing to chown -R" in (r.stdout + r.stderr)
        assert (foreign / "precious.txt").read_text() == "do not chown me"  # untouched (dry run)
    finally:
        shutil.rmtree(foreign, ignore_errors=True)


def test_the_symlink_parent_is_created_before_linking_when_the_base_is_elsewhere():
    assert 'run mkdir -p "$(dirname "$BASE")"; run ln -s "$REAL" "$BASE"' in CODE


def test_the_default_phase_is_two_vms_and_the_credential_vm_needs_an_explicit_flag():
    assert 'DAEMONS="com.asta.socket_vmnet com.asta.vm.fw com.asta.vm.app"' in CODE
    assert '[ "$WITHCRED" = 1 ] && DAEMONS="$DAEMONS com.asta.vm.cred"' in CODE
    out = subprocess.run(["/bin/sh", str(SCRIPT)], capture_output=True, text=True, timeout=60)
    if sys.platform != "darwin":  # the host script refuses to run anywhere but an Apple-silicon Mac
        assert out.returncode == 1 and "REFUSING" in out.stderr
        return
    assert out.returncode == 0 and "com.asta.vm.cred.plist /Library" not in out.stdout
    assert (
        "com.asta.vm.fw.plist /Library" in out.stdout
        and "com.asta.vm.app.plist /Library" in out.stdout
    )
