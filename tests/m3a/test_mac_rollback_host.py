"""Rollback script (security-host/mac/rollback-host.sh): safe by default, never destructive by accident."""

import re
import subprocess
import sys
from pathlib import Path

MAC = Path(__file__).resolve().parents[2] / "infra" / "security-host" / "mac"
SCRIPT = MAC / "rollback-host.sh"
TEXT = SCRIPT.read_text(encoding="utf-8")
CODE = "\n".join(ln for ln in TEXT.splitlines() if not ln.lstrip().startswith("#"))
# only the lines that execute something (not the explanatory `say` text)
ACTIONS = "\n".join(
    ln
    for ln in CODE.splitlines()
    if re.match(r"\s*(if .*; then )?(run|launchctl|dscl)\b|.*\brun (rm|launchctl|dscl|/usr)", ln)
)


def sh(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/sh", str(SCRIPT), *args], capture_output=True, text=True, timeout=60
    )


def test_dry_run_is_the_default_apply_needs_root_and_a_dry_run_changes_nothing():
    assert "APPLY=0" in CODE and re.search(r'\[ "\$APPLY" = 1 \] && \[ "\$\(id -u\)" != 0 \]', CODE)
    daemons = Path("/Library/LaunchDaemons")
    before = sorted(p.name for p in daemons.glob("com.asta.*"))
    out = sh()
    if sys.platform != "darwin":  # macOS-only script: it must refuse elsewhere
        assert out.returncode == 1 and "macOS only" in out.stderr
        return
    assert out.returncode == 0 and "nothing was changed" in out.stdout
    assert sorted(p.name for p in daemons.glob("com.asta.*")) == before
    r = sh("--apply")
    assert r.returncode == 1 and "needs root" in (
        r.stdout + r.stderr
    )  # non-root: refused, nothing done


def test_the_default_rollback_keeps_data_account_socket_vmnet_and_firewall():
    for flag in ("--purge-data", "--purge-socket-vmnet", "--remove-account", "--restore-firewall"):
        assert flag in CODE
    assert re.search(
        r'if \[ "\$PURGE" = 1 \]; then[\s\S]*rm -rf', CODE
    )  # destructive steps behind flags
    assert re.search(r'if \[ "\$SOCK" = 1 \]; then run rm -rf /opt/socket_vmnet', CODE)
    assert "orphan files" in CODE  # account removal refused while data remains


def test_it_never_touches_other_projects_or_machine_wide_settings():
    assert ACTIONS  # the filter found the executable lines
    for banned in (
        "docker",
        "brew",
        "pmset",
        "fdesetup",
        "networksetup",
        "systemsetup",
        "killall",
        "pkill",
    ):
        assert not re.search(rf"(?<![\w/.-]){banned}\b", ACTIONS), banned
    for path in (p.rstrip(";") for p in re.findall(r"rm -rf? (\S+)", ACTIONS)):
        ok = path in {'"$BASE"', '"$T"', "/opt/socket_vmnet"} or path.startswith(
            ('"/Library/LaunchDaemons', '"$LIBEXEC')
        )
        assert ok, path


def test_it_unloads_the_vms_before_their_uplink_and_names_only_asta_daemons():
    m = re.search(r'DAEMONS="([^"]+)"', CODE)
    assert m is not None
    assert m.group(1).split() == [
        "com.asta.vm.cred",
        "com.asta.vm.app",
        "com.asta.vm.fw",
        "com.asta.socket_vmnet",
    ]
    assert set(m.group(1).split()) == {
        p.stem for p in (MAC / "launchd").glob("*.plist")
    }  # what provisioning installs
