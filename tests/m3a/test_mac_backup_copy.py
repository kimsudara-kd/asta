"""Host-side copy of the encrypted backup image to an external drive (fail-closed)."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "infra"
    / "security-host"
    / "mac"
    / "backup-image-to-external.sh"
)
TEXT = SCRIPT.read_text(encoding="utf-8")
CODE = "\n".join(ln for ln in TEXT.splitlines() if not ln.lstrip().startswith("#"))
pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="macOS tools (stat -f, mount, lsof)"
)


def sh(args, env_extra=None):
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(  # noqa: S603  (fixed argv: repo-owned script, throw-away paths)
        ["/bin/sh", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        check=False,
    )


@pytest.fixture
def world(tmp_path):
    img = tmp_path / "asta-backup.img"
    img.write_bytes(os.urandom(3 * 1024 * 1024))
    vol = tmp_path / "vol"
    dest = vol / "asta-backups"
    vol.mkdir()
    env = {"ASTA_BACKUP_IMAGE": str(img), "ASTA_BACKUP_MOUNT_PREFIX": str(tmp_path) + "/"}
    return img, vol, dest, env


def test_it_is_static_fail_closed_and_never_downloads_or_deletes_arbitrary_files():
    assert "APPLY=0" in CODE and "[dry-run]" in CODE
    assert "is not mounted" in CODE and 'mount | grep -qF " on $VOL ("' in CODE
    assert "lsof" in CODE and "shasum -a 256" in CODE and ".part" in CODE
    for banned in ("curl", "wget", "rm -rf", "diskutil", "sudo "):
        assert banned not in CODE, banned
    assert "asta-backup-[0-9]*T[0-9]*Z.img" in CODE  # the only pattern it ever prunes


def test_refuses_a_destination_outside_a_mounted_external_volume():
    out = sh(["--dest", "/Users/Shared/x"], {"ASTA_BACKUP_IMAGE": "/dev/null"})
    assert out.returncode != 0 and "must be inside a mounted external volume" in out.stderr


def test_refuses_an_unplugged_drive_instead_of_writing_to_the_internal_disk(world):
    img, _, _, _ = world
    out = sh(
        ["--dest", "/Volumes/asta-unplugged-drive/asta-backups"], {"ASTA_BACKUP_IMAGE": str(img)}
    )
    assert out.returncode != 0 and "is not mounted" in out.stderr
    assert not Path("/Volumes/asta-unplugged-drive").exists()


def test_dry_run_changes_nothing(world):
    _, vol, dest, env = world
    out = sh(["--dest", str(dest)], env)
    assert out.returncode == 0 and "DRY-RUN" in out.stdout and "nothing was changed" in out.stdout
    assert not dest.exists()


def test_apply_makes_a_verified_copy_and_prunes_only_its_own_files(world):
    img, vol, dest, env = world
    dest.mkdir()
    keep_a, keep_b = dest / "holiday-photo.jpg", dest / "asta-backup-notes.txt"
    keep_a.write_bytes(b"x")
    keep_b.write_bytes(b"y")
    for _ in range(3):
        out = sh(["--dest", str(dest), "--apply", "--keep", "2"], env)
        assert out.returncode == 0, out.stdout + out.stderr
        assert "verified copy" in out.stdout
        time.sleep(1.1)  # timestamps have one-second resolution
    copies = sorted(dest.glob("asta-backup-2*Z.img"))
    assert len(copies) == 2  # retention kept the newest two
    assert all(c.read_bytes() == img.read_bytes() for c in copies)
    assert all((c.parent / (c.name + ".sha256")).exists() for c in copies)
    assert not list(dest.glob("*.part"))
    assert keep_a.read_bytes() == b"x" and keep_b.read_bytes() == b"y"  # unrelated files untouched


def test_refuses_while_the_vm_has_the_image_open(world):
    img, _, dest, env = world
    with img.open("rb"):  # same effect as a running VM holding the disk
        out = sh(["--dest", str(dest), "--apply"], env)
    assert out.returncode != 0 and "is open" in out.stderr and not dest.exists()
