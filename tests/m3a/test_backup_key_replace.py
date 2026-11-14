"""backup-key-replace.sh: the identity can only be created on external media, never overwrites, never touches the old key."""

import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "infra/security-host/mac/backup-key-replace.sh"
CODE = "\n".join(ln for ln in SCRIPT.read_text().splitlines() if not ln.lstrip().startswith("#"))
# executable lines only (the messages printed for the owner may mention the old key by name)
ACTIONS = "\n".join(
    ln
    for ln in CODE.splitlines()
    if not re.match(r"\s*(echo|printf|die|\S+\) die)\b", ln) and "die " not in ln
)


def sh(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/sh", str(SCRIPT), *args], capture_output=True, text=True, timeout=60
    )  # noqa: S603


def test_the_script_never_reads_moves_or_deletes_the_old_identity_or_uses_the_network():
    for banned in (
        'asta-backup-identity"',
        "~/asta-backup-identity",
        "$HOME/asta-backup-identity",
        "curl",
        "wget",
        "sudo",
        "ssh ",
        "rm -rf /",
        "shred",
    ):
        assert banned not in ACTIONS, banned
    assert not re.search(r"\b(mv|cp)\b.*identity", ACTIONS)
    assert (
        "refusing to overwrite" in CODE
        and "--confirm-offline" in CODE
        and "Device Location" in CODE
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="uses diskutil")
def test_it_refuses_non_media_locations_and_internal_volumes_in_a_dry_run(tmp_path):
    assert sh().returncode == 1  # --media is required
    for bad in (
        "/Users/Shared/x",
        "/opt/x",
        str(tmp_path),
        "/System/Volumes/Data",
        "/Volumes/__does_not_exist__",
    ):
        r = sh("--media", bad, "--recipient-out", str(tmp_path / "r.pub"))
        assert r.returncode == 1 and "REFUSING" in r.stderr, bad
    assert not (tmp_path / "r.pub").exists()  # nothing is created on a refusal or a dry run


@pytest.mark.skipif(sys.platform != "darwin", reason="uses diskutil")
def test_apply_requires_the_offline_confirmation_even_on_acceptable_media():
    r = sh("--media", "/Volumes/__none__", "--apply")
    assert r.returncode == 1 and "REFUSING" in r.stderr
