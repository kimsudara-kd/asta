"""The single-Mac install files: dry-run by default, root for --apply, minimal copy list, valid plist, sane template."""

from __future__ import annotations

import json
import os
import plistlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
NOVM = ROOT / "infra/no-vm"
INSTALL = NOVM / "install-no-vm.sh"
UNINSTALL = NOVM / "uninstall-no-vm.sh"


def sh(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/sh", str(script), *args], capture_output=True, text=True, timeout=60
    )  # noqa: S603


@pytest.mark.skipif(
    sys.platform != "darwin", reason="the installer is macOS only (it refuses elsewhere)"
)
def test_dry_run_is_the_default_and_changes_nothing_and_apply_needs_root():
    out = sh(INSTALL)
    assert out.returncode == 0 and "nothing was changed" in out.stdout and "[dry-run]" in out.stdout
    assert "auth.key" in out.stdout and "never printed" in out.stdout
    if os.getuid() != 0:
        r = sh(INSTALL, "--apply", "--python", "/usr/bin/python3")
        assert r.returncode == 1 and "needs root" in r.stderr
        r = sh(UNINSTALL, "--apply")
        assert r.returncode == 1 and "needs root" in r.stderr
    assert sh(INSTALL, "--bogus").returncode == 2
    assert sh(UNINSTALL).returncode == 0 and "nothing was changed" in sh(UNINSTALL).stdout


@pytest.mark.skipif(
    sys.platform != "darwin", reason="the installer is macOS only (it refuses elsewhere)"
)
def test_dry_run_leaves_the_machine_untouched():
    for p in ("/opt/asta-cred", "/etc/asta-cred", "/var/asta-cred", "/var/asta-cred-run"):
        before = os.path.exists(p)
        sh(INSTALL)
        assert os.path.exists(p) == before


def test_the_scripts_only_remove_the_paths_they_own_and_never_touch_other_things():
    text = INSTALL.read_text() + UNINSTALL.read_text()
    assert "rm -rf" not in INSTALL.read_text()
    for m in re.finditer(r"rm -(?:rf|f) ([^\n;&|]+)", UNINSTALL.read_text()):
        for tok in m.group(1).split():
            assert tok.strip('"') in {
                '"$CODE"',
                "$CODE",
                "$LOG",
                "$RUN",
                "$ETC",
                "$VAR",
                "$PLIST",
                '"$ETC/cred.json"',
                "$ETC/cred.json",
                "-f",
                "-rf",
            } or tok.startswith(('"$', "$")), tok
    for bad in (
        "pfctl",
        "socketfilterfw",
        "systemsetup",
        "fdesetup",
        "defaults write",
        "docker",
        "launchctl disable",
    ):
        assert bad not in text, (
            bad
        )  # nothing outside the service's own files and users is ever touched


def test_the_launchd_plist_runs_as_the_credential_user_with_a_private_python():
    d = plistlib.loads((NOVM / "com.asta.cred.plist").read_bytes())
    assert (
        d["Label"] == "com.asta.cred"
        and d["UserName"] == "asta-cred"
        and d["GroupName"] == "asta-ipc"
    )
    assert d["ProgramArguments"][:2] == ["/opt/asta-cred/venv/bin/python", "-I"]
    assert d["ProgramArguments"][-2:] == ["--config", "/etc/asta-cred/cred.json"]
    assert d["KeepAlive"] is True and d["RunAtLoad"] is True
    assert "EnvironmentVariables" not in d and "Password" not in json.dumps(d)


def _copy_lists() -> tuple[list[str], list[str]]:
    text = INSTALL.read_text()
    svc = re.search(r"for f in (connector.py[^;]+); do", text)
    asta = re.search(r"for f in (__init__.py core/__init__.py[^;]+); do", text, re.S)
    assert svc and asta
    return svc.group(1).split(), asta.group(1).replace("\\\n", " ").split()


def test_the_copy_list_is_exactly_what_the_service_needs_to_import_nothing_else_of_asta_is_installed():
    svc, asta = _copy_lists()
    tmp = Path(tempfile.mkdtemp()).resolve()
    for f in svc:
        (tmp / "service").mkdir(exist_ok=True)
        (tmp / "service" / f).write_bytes(
            (ROOT / "proposals/binance_testnet_signer" / f).read_bytes()
        )
    for f in asta:
        dst = tmp / "asta" / f
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes((ROOT / "src/asta" / f).read_bytes())
    code = f"""
import sys
class Block:
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in {{'sqlalchemy','pg8000','fastapi','pydantic','pydantic_settings','httpx','alembic'}}:
            raise ImportError('not installed in the service environment: ' + name)
sys.meta_path.insert(0, Block())
sys.path.insert(0, {str(tmp)!r})
sys.path.insert(0, {str(tmp / "service")!r})
import run_service
bad = [m.__file__ for n, m in sys.modules.items() if n.split('.')[0] == 'asta' and getattr(m, '__file__', None) and not m.__file__.startswith({str(tmp)!r})]
print('OUTSIDE:' + repr(bad))
"""
    r = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60
    )  # noqa: S603
    assert r.returncode == 0, r.stderr
    assert "OUTSIDE:[]" in r.stdout, r.stdout


def test_the_pinned_dependency_matches_the_lockfile_and_the_config_template_loads():
    text = INSTALL.read_text()
    pinned = re.search(r'"cryptography==([0-9.]+)"', text)
    assert pinned
    lock = (ROOT / "uv.lock").read_text()
    assert re.search(rf'name = "cryptography"\nversion = "{re.escape(pinned.group(1))}"', lock)
    body = re.search(r"<<JSON\n(.*?)\nJSON", text, re.S)
    assert body
    cfg_text = body.group(1).replace("$RUN", "/var/asta-cred-run").replace("$ETC", "/etc/asta-cred")
    cfg_text = cfg_text.replace("$VAR", "/var/asta-cred").replace("$APPUID", "501")
    sys.path.insert(0, str(ROOT / "proposals/binance_testnet_signer"))
    import run_service as rsv  # type: ignore[import-not-found]

    p = Path(tempfile.mkdtemp()) / "cred.json"
    p.write_text(cfg_text)
    cfg = rsv.load_config(str(p))
    assert cfg["mode"] == "mock" and cfg["allowed_uids"] == [501]
    assert cfg["socket"].startswith("/var/asta-cred-run/")  # outside the 0700 state directory
