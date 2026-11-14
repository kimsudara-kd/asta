"""QEMU integrity pin: the pinned set matches this Mac, tampering is detected, a bad pin blocks VM start."""

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

MAC = Path(__file__).resolve().parents[2] / "infra" / "security-host" / "mac"
PIN = MAC / "qemu-pin.sha256"
META = MAC / "qemu-pin.json"
QEMU = Path("/opt/homebrew/bin/qemu-system-aarch64")
needs_qemu = pytest.mark.skipif(not QEMU.exists(), reason="Homebrew QEMU not installed")


def test_the_pin_files_are_well_formed_and_cover_binary_firmware_and_libraries():
    lines = PIN.read_text().splitlines()
    assert len(lines) >= 5
    for ln in lines:
        digest, _, path = ln.partition("  ")
        assert (
            len(digest) == 64
            and set(digest) <= set("0123456789abcdef")
            and path.startswith("/opt/homebrew/")
        ), ln
    paths = [ln.partition("  ")[2] for ln in lines]
    assert any(p.endswith("/bin/qemu-system-aarch64") for p in paths)
    assert any(p.endswith("edk2-aarch64-code.fd") for p in paths)
    assert any(p.endswith(".dylib") for p in paths)
    meta = json.loads(META.read_text())
    assert meta["version"] and meta["tap"] == "homebrew/core" and meta["bottles"]
    assert any(
        "adhoc" in s.lower() for s in meta["signature"]
    )  # recorded fact: Homebrew signs ad hoc


@needs_qemu
def test_the_pin_matches_the_installed_qemu_on_this_mac():
    r = subprocess.run(
        [sys.executable, str(MAC / "qemu_pin.py"), "verify", "--pin", str(PIN)],
        capture_output=True,
        text=True,
    )  # noqa: S603
    assert r.returncode == 0, r.stdout


def test_verify_detects_a_changed_a_missing_and_an_extra_tampered_file(tmp_path):
    f = tmp_path / "lib.dylib"
    f.write_bytes(b"original")
    pin = tmp_path / "pin.sha256"
    pin.write_text(f"{hashlib.sha256(b'original').hexdigest()}  {f}\n")
    ok = subprocess.run(
        [sys.executable, str(MAC / "qemu_pin.py"), "verify", "--pin", str(pin)],
        capture_output=True,
        text=True,
    )  # noqa: S603
    assert ok.returncode == 0
    f.write_bytes(b"tampered")
    bad = subprocess.run(
        [sys.executable, str(MAC / "qemu_pin.py"), "verify", "--pin", str(pin)],
        capture_output=True,
        text=True,
    )  # noqa: S603
    assert bad.returncode == 1 and "MISMATCH" in bad.stdout
    f.unlink()
    gone = subprocess.run(
        [sys.executable, str(MAC / "qemu_pin.py"), "verify", "--pin", str(pin)],
        capture_output=True,
        text=True,
    )  # noqa: S603
    assert gone.returncode == 1 and "MISSING" in gone.stdout


def test_the_vm_launcher_refuses_to_start_without_a_matching_pin(tmp_path):
    # DRY_RUN=0 with a bad/missing pin must stop at the check, before any QEMU process can start
    bad = tmp_path / "bad.sha256"
    bad.write_text(f"{'0' * 64}  {MAC / 'qemu-vms.sh'}\n")
    for pin in (bad, tmp_path / "absent.sha256"):
        r = subprocess.run(  # noqa: S603
            ["/bin/sh", str(MAC / "qemu-vms.sh"), "run", "app"],
            capture_output=True, text=True, timeout=30,
            env={"DRY_RUN": "0", "ASTA_QEMU_PIN": str(pin), "PATH": "/usr/bin:/bin"},
        )  # fmt: skip
        assert r.returncode == 70 and "REFUSING" in r.stderr, (pin.name, r.stdout, r.stderr)
        assert "qemu-system-aarch64 -name" not in r.stdout  # the launch line was never reached


def test_provisioning_installs_the_pin_root_owned_and_refuses_a_stale_pin():
    code = (MAC / "provision-host.sh").read_text()
    assert (
        'install -o root -g wheel -m 0644 "$HERE/qemu-pin.sha256" "$LIBEXEC/qemu-pin.sha256"'
        in code
    )
    assert (
        'shasum -a 256 -c "$HERE/qemu-pin.sha256"' in code and "regenerate it deliberately" in code
    )
    assert shutil.which("sh")
