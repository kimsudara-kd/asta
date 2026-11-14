"""Backup-key relocation rehearsal: synthetic material only, never the real identity."""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "infra/security-host/mac/backup-key-rehearsal.sh"
CODE = "\n".join(ln for ln in SCRIPT.read_text().splitlines() if not ln.lstrip().startswith("#"))
REAL = Path.home() / "asta-backup-identity" / "identity.txt"


def test_the_script_never_references_the_real_identity_or_the_network():
    for banned in (
        "asta-backup-identity",
        "backup-recipient.pub",
        "curl",
        "wget",
        "ssh",
        "pg_dump",
        "psql",
        "sudo",
    ):
        assert banned not in CODE, banned
    assert "mktemp -d" in CODE and "trap 'rm -rf" in CODE and "chmod 700" in CODE


@pytest.mark.skipif(
    not (shutil.which("age") and shutil.which("age-keygen")), reason="age not installed"
)
def test_the_rehearsal_passes_and_leaves_the_real_identity_untouched(tmp_path):
    def fingerprint():  # metadata only: the private key file is never opened by a test
        return (
            (st.st_ino, st.st_size, st.st_mtime_ns)
            if (st := REAL.stat() if REAL.exists() else None)
            else None
        )

    before = fingerprint()
    report = tmp_path / "r.json"
    r = subprocess.run(
        ["/bin/sh", str(SCRIPT), str(report)], capture_output=True, text=True, timeout=120
    )  # noqa: S603
    assert r.returncode == 0, r.stdout + r.stderr
    d = json.loads(report.read_text())
    assert d["all_passed"] is True and d["real_identity_touched"] is False
    assert all(v is True for k, v in d.items() if k[0].isdigit())
    assert before == fingerprint()
    assert not list(Path(tempfile.gettempdir()).glob("asta-keyrehearsal.*")) and not list(
        tmp_path.glob("asta-keyrehearsal.*")
    )  # cleaned up
