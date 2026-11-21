"""R-07: the publish gate must fail closed (a broken repo path or an empty map is never 'clean')."""

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "privacy_scan.py"


def _run(*args: str):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)  # noqa: S603


def test_bad_repo_path_and_empty_map_are_errors_not_clean(tmp_path: Path):
    m = tmp_path / "map.tsv"
    m.write_text("secret-name\tP9\n", encoding="utf-8")
    r = _run("--repo", str(tmp_path / "nope"), "--map", str(m))
    assert r.returncode == 2 and "clean" not in r.stdout
    empty = tmp_path / "empty.tsv"
    empty.write_text("", encoding="utf-8")
    repo = Path(__file__).resolve().parents[1]
    r = _run("--repo", str(repo), "--map", str(empty))
    assert r.returncode == 2 and "clean" not in r.stdout
