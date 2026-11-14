"""Synthetic vault recovery rehearsal: passes, leaves no temp directory and creates no real credential."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "infra/security-host/acceptance/vault_recovery_rehearsal.py"


def test_the_rehearsal_passes_all_checks_and_cleans_up(tmp_path):
    report = tmp_path / "r.json"
    before = set(Path(tempfile.gettempdir()).glob("asta-vault-recovery-*"))
    r = subprocess.run(
        [sys.executable, str(SCRIPT), str(report)],
        capture_output=True,
        text=True,
        timeout=120,
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"},
    )  # noqa: S603
    assert r.returncode == 0, r.stdout + r.stderr
    d = json.loads(report.read_text())
    assert (
        d["real_credentials"] is False
        and d["failed"] == 0
        and d["passed"] == len(d["checks"]) == 11
    )
    assert (
        set(Path(tempfile.gettempdir()).glob("asta-vault-recovery-*")) == before
    )  # temp directory removed


def test_the_rehearsal_source_has_no_literal_credentials_or_network():
    text = SCRIPT.read_text()
    for banned in (
        "http",
        "socket",
        "requests",
        "httpx",
        "subprocess",
        "os.system",
        "BEGIN PRIVATE",
    ):
        assert banned not in text, banned
