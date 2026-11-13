"""M3A-R3: reproducible provisioning, deployment/rollback and acceptance artifacts (nothing is deployed)."""

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _git_bash() -> str | None:
    """Git for Windows bash only. NEVER `bash` from PATH: on this PC that is the WSL launcher, which
    would start the shared Ubuntu distribution that belongs to other work."""
    for c in (
        "C:/Program Files/Git/bin/bash.exe",
        "C:/Program Files/Git/usr/bin/bash.exe",
        "/bin/bash",
        "/usr/bin/bash",
    ):
        if Path(c).exists():
            return c
    return None


BASH = _git_bash()
PROV = ROOT / "infra" / "security-host" / "provision"
ACC = ROOT / "infra" / "security-host" / "acceptance"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_seed_build_is_reproducible_and_carries_the_reviewed_configuration(tmp_path):
    b = load(PROV / "build_seed.py", "build_seed")
    s1 = b.emit(tmp_path / "a")
    s2 = b.emit(tmp_path / "b")
    assert s1 == s2  # byte-for-byte reproducible
    for vm, host in (("firewall-vm", "asta-fw"), ("runtime-vm", "asta-rt")):
        text = (tmp_path / "a" / vm / "user-data").read_text(encoding="utf-8")
        assert text.startswith("#cloud-config\n")
        cfg = json.loads(text.split("\n", 1)[1])
        assert (
            cfg["hostname"] == host and cfg["ssh_pwauth"] is False and cfg["disable_root"] is True
        )
        assert cfg["users"][0]["lock_passwd"] is True
        assert cfg["users"][0]["ssh_authorized_keys"] == ["__OPERATOR_SSH_PUBLIC_KEY__"]
        files = {w["path"]: w["content"] for w in cfg["write_files"]}
        nft = (ROOT / "infra" / "security-host" / vm / "nftables.conf").read_text(encoding="utf-8")
        assert files["/etc/nftables.conf"] == nft.replace("\r\n", "\n")
        assert ["nft", "-c", "-f", "/etc/nftables.conf"] in cfg["runcmd"]
        assert "ssh-rsa" not in text and "BEGIN " not in text  # no key material embedded
    rt = json.loads((tmp_path / "a" / "runtime-vm" / "user-data").read_text().split("\n", 1)[1])
    enabled = " ".join(" ".join(c) for c in rt["runcmd"])
    assert (
        "asta-vault" not in enabled and "asta-connector" not in enabled
    )  # M3-B modules not enabled
    fw = json.loads((tmp_path / "a" / "firewall-vm" / "user-data").read_text().split("\n", 1)[1])
    assert any("proxy.py" in w["path"] for w in fw["write_files"])
    sums = (tmp_path / "a" / "INPUT-SHA256SUMS").read_text(encoding="utf-8")
    assert (
        "infra/egress/proxy.py" in sums and "infra/security-host/firewall-vm/nftables.conf" in sums
    )


def test_hypervisor_scripts_are_dry_run_by_default_and_never_target_this_pc():
    ps = (PROV / "hyperv-create.ps1").read_text(encoding="utf-8")
    assert (
        "[switch]$Execute" in ps
        and "if ($Execute)" in ps
        and "DO NOT RUN ON THE DEVELOPMENT PC" in ps
    )
    assert "-SwitchType Private" in ps and "-AllowManagementOS `$false" in ps
    for f in ("virtualbox-create.sh", "libvirt-create.sh", "deploy.sh", "rollback.sh"):
        t = (PROV / f).read_text(encoding="utf-8")
        assert 'DRY_RUN="${DRY_RUN:-1}"' in t, f
    vb = (PROV / "virtualbox-create.sh").read_text(encoding="utf-8")
    assert (
        "--nic1 intnet --intnet1 asta-prot" in vb
        and "--nested-hw-virt off" in vb
        and "--clipboard-mode disabled" in vb
    )
    assert "<forward" not in (PROV / "libvirt-create.sh").read_text(
        encoding="utf-8"
    )  # isolated network


@pytest.mark.skipif(BASH is None, reason="Git Bash not available")
@pytest.mark.parametrize(
    "script", ["deploy.sh", "rollback.sh", "virtualbox-create.sh", "libvirt-create.sh"]
)
def test_shell_scripts_have_valid_syntax(script):
    r = subprocess.run([BASH, "-n", str(PROV / script)], capture_output=True, text=True)  # noqa: S603,S607
    assert r.returncode == 0, r.stderr


@pytest.mark.skipif(BASH is None, reason="Git Bash not available")
def test_deploy_dry_run_prints_the_plan_and_changes_nothing():
    r = subprocess.run(  # noqa: S603,S607
        [BASH, str(PROV / "deploy.sh"), "firewall", str(ROOT)],
        capture_output=True,
        text=True,
        env={"PATH": __import__("os").environ["PATH"], "DRY_RUN": "1"},
    )
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "nft -c -f /etc/nftables.conf" in out  # check-before-load is part of the plan
    assert out.index("nft -c -f /etc/nftables.conf") < out.index(
        "systemctl enable --now nftables.service"
    )
    assert "Applied (DRY_RUN=1)" in out
    rb = subprocess.run(  # noqa: S603,S607
        [BASH, str(PROV / "rollback.sh")],
        capture_output=True,
        text=True,
        env={"PATH": __import__("os").environ["PATH"], "DRY_RUN": "1"},
    )
    assert (
        rb.returncode == 1 and "no backup recorded" in rb.stderr
    )  # refuses without a recorded backup


@pytest.mark.skipif(not shutil.which("powershell"), reason="PowerShell not available")
def test_hyperv_script_parses_without_errors():
    cmd = (
        "$e=$null;$t=$null;[void][System.Management.Automation.Language.Parser]::ParseFile("
        f"'{PROV / 'hyperv-create.ps1'}',[ref]$t,[ref]$e); if($e.Count){{$e|%{{$_.Message}};exit 1}}"
    )
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True
    )  # noqa: S603,S607
    assert r.returncode == 0, r.stdout + r.stderr


def test_vm_acceptance_is_importable_and_its_parsers_work():
    m = load(ACC / "vm_acceptance.py", "vm_acceptance")
    rs = "chain input {\n type filter hook input priority 0; policy drop;\n}\nchain output { policy drop; }\nchain forward { policy drop; }"
    assert len(m.policies(rs)) == 3
    assert m.default_routes("default via 10.0.0.1 dev eth0\n10.77.0.0/24 dev prot0") == [
        "default via 10.0.0.1 dev eth0"
    ]
    assert m.default_routes("10.77.0.0/24 dev prot0 proto kernel") == []
    src = (ACC / "vm_acceptance.py").read_text(encoding="utf-8")
    for needle in (
        "boot_id changed",
        "ruleset identical after reboot",
        "NO default route",
        "unprivileged process",
    ):
        assert needle in src


def test_the_negative_suite_runs_against_any_proxy_address():
    src = (ROOT / "infra" / "egress" / "negative_tests.py").read_text(encoding="utf-8")
    assert 'os.environ.get("ASTA_PROXY"' in src and "PROXY[0]" in src


def test_synthetic_secret_vault_validation_passes_here(tmp_path):
    out = tmp_path / "vv.json"
    r = subprocess.run(  # noqa: S603
        [sys.executable, str(ACC / "vault_validation.py"), str(out), str(tmp_path / "w")],
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    d = json.loads(out.read_text())
    assert not d["failed"] and d["synthetic_secrets_generated"] == 5
    # no synthetic secret value may appear in the report itself
    assert not re.search(r"SYNTH-[A-Za-z0-9_-]{20,}", out.read_text())
    if sys.platform == "win32":  # POSIX-only checks must be SKIPPED, never silently passed
        assert len(d["skipped"]) == 4
