"""M3A-R1 D: accepted M0-M2 evidence is frozen, and test runs cannot write into it."""

import importlib.util
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load():
    spec = importlib.util.spec_from_file_location(
        "vfe", ROOT / "scripts" / "verify_frozen_evidence.py"
    )
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_the_real_accepted_evidence_is_intact():
    assert load().verify() == []


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)  # noqa: S603,S607


def test_tampering_addition_removal_and_tag_moves_are_detected(tmp_path, monkeypatch):
    m = load()
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.invalid")
    _git(tmp_path, "config", "user.name", "t")
    ev = tmp_path / "docs" / "evidence" / "m2"
    ev.mkdir(parents=True)
    (ev / "report.md").write_text("accepted result\n", encoding="utf-8")
    (ev / "shot.png").write_bytes(b"\x89PNG-original")
    (tmp_path / "docs" / "evidence" / "M2-ACCEPTANCE-RECORD.md").write_text(
        "record\n", encoding="utf-8"
    )
    (tmp_path / "docs" / "evidence" / "M2-acceptance.md").write_text("details\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "accepted")
    _git(tmp_path, "tag", "-a", "t2", "-m", "t2")
    monkeypatch.setattr(m, "ROOT", tmp_path)
    monkeypatch.setattr(m, "MANIFEST", tmp_path / "docs" / "evidence" / "FROZEN-EVIDENCE.json")
    monkeypatch.setattr(
        m,
        "FROZEN",
        {
            "M2": {
                "tag": "t2",
                "dirs": ["docs/evidence/m2"],
                "files": [
                    "docs/evidence/M2-ACCEPTANCE-RECORD.md",
                    "docs/evidence/M2-acceptance.md",
                ],
            }
        },
    )
    m.MANIFEST.write_text(__import__("json").dumps(m.build()), encoding="utf-8")
    assert m.verify() == []
    # 1) a re-generated screenshot (what an e2e run used to do)
    (ev / "shot.png").write_bytes(b"\x89PNG-regenerated")
    assert any("MODIFIED" in p and "shot.png" in p for p in m.verify())
    (ev / "shot.png").write_bytes(b"\x89PNG-original")
    assert m.verify() == []
    # 2) an edited report
    (ev / "report.md").write_text("accepted result (edited)\n", encoding="utf-8")
    assert any("report.md" in p for p in m.verify())
    (ev / "report.md").write_text("accepted result\n", encoding="utf-8")
    # 3) an added file and a removed file
    (ev / "new.png").write_bytes(b"x")
    assert any("UNEXPECTED" in p for p in m.verify())
    (ev / "new.png").unlink()
    (ev / "report.md").unlink()
    assert any("missing" in p for p in m.verify())
    (ev / "report.md").write_text("accepted result\n", encoding="utf-8")
    # 4) the tag moved
    (tmp_path / "other.txt").write_text("x", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "later")
    _git(tmp_path, "tag", "-f", "-a", "t2", "-m", "moved")
    assert any("moved" in p for p in m.verify())


def test_e2e_cannot_write_screenshots_into_frozen_evidence():
    helpers = (ROOT / "apps/web/e2e/helpers.ts").read_text(encoding="utf-8")
    run = (ROOT / "apps/web/e2e/run.mjs").read_text(encoding="utf-8")
    # the default and the runner's output are outside docs/evidence/{m0-r1,m1,m1-r1,m2}
    assert "docs/evidence/m2" not in helpers and "docs/evidence/m2" not in run
    assert 'FROZEN = new Set(["m0-r1", "m1", "m1-r1", "m2"])' in run
    m = re.search(r"const FROZEN_EVIDENCE = /(.+)/;", helpers)
    assert m
    rx = re.compile(m.group(1).replace("(m0-r1|m1|m1-r1|m2)", "(m0-r1|m1|m1-r1|m2)"))
    for frozen in (
        "docs/evidence/m2/screenshots",
        "docs\\evidence\\m1-r1\\x",
        "docs/evidence/m0-r1",
    ):
        assert rx.search(frozen), frozen
    for ok in ("data/e2e-shots/m3a/run-1", "docs/evidence/m3a/screenshots/run-1"):
        assert not rx.search(ok), ok
    assert "E2E_PUBLISH" in run and "shotsDir" in run
