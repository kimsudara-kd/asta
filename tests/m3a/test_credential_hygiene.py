"""M3A-R1/R2 C: development credential hygiene.

``data/manual/creds.json`` (obsolete seeded app-user logins from a 2026-11-12 manual run, SHA-256
prefix 9412ad3eb6fa6d76) was securely removed in M3A-R2. This test is its tombstone: that path, and
any other credential-like file outside source/docs, must not exist, and the gitleaks suppression
list must stay exactly as reviewed (two single-fingerprint entries, no rule/path allowlist growth).
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKIP = {"node_modules", ".venv", ".git", ".next"}
NAME = re.compile(r"(cred|secret|password|passwd|token|apikey|api_key|private)", re.IGNORECASE)
EXT = {".json", ".txt", ".env", ".yaml", ".yml", ".ini", ".cfg", ".key", ".pem"}

REMOVED_TOMBSTONES = ("data/manual/creds.json",)  # sha256 prefix 9412ad3eb6fa6d76 when it existed


def credential_like_files() -> list[Path]:
    out = []
    for p in ROOT.rglob("*"):
        if any(part in SKIP for part in p.parts) or not p.is_file():
            continue
        # the e2e runner writes and deletes its own throw-away file while a run is in progress
        if p.name == ".secrets.json" and p.parent.name == "e2e":
            continue
        if p.suffix.lower() in EXT and NAME.search(p.name):
            out.append(p)
    return out


def test_no_credential_like_file_exists_and_the_removed_one_stays_removed():
    for rel in REMOVED_TOMBSTONES:
        assert not (ROOT / rel).exists(), f"removed obsolete credential file reappeared: {rel}"
    problems = []
    for p in credential_like_files():
        rel = p.relative_to(ROOT).as_posix()
        # source/documentation files that merely have such a word in their name are not credentials
        if rel.startswith(("src/", "tests/", "docs/", "scripts/", "infra/", "apps/web/src/")):
            continue
        problems.append(f"unexpected credential-like file: {rel}")
    assert not problems, problems


def test_gitleaks_suppression_is_exactly_the_five_reviewed_fingerprints():
    entries = [
        ln.strip()
        for ln in (ROOT / ".gitleaksignore").read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.startswith("#")
    ]
    assert sorted(entries) == sorted(
        [
            "23cc0228cdde01dd86469a92ebda1fcf78229c01:tests/m3a/test_security.py:generic-api-key:248",
            "968252f427e213bf04c3b87d57ebdfdfc65d0811:tests/m3a/test_credential_hygiene.py:generic-api-key:19",
            # 2026-11-14 owner decision: 3 SHA-256 manifest digests (not secrets)
            "e71706faf67bad42bb117a24b38db334fdcaf4e5:docs/evidence/m3a/REVIEW-MANIFEST.json:generic-api-key:43",
            "e71706faf67bad42bb117a24b38db334fdcaf4e5:docs/evidence/m3a/REVIEW-MANIFEST.json:generic-api-key:44",
            "e71706faf67bad42bb117a24b38db334fdcaf4e5:docs/evidence/m3a/REVIEW-MANIFEST.json:generic-api-key:82",
        ]
    )
    toml = (ROOT / ".gitleaks.toml").read_text(encoding="utf-8")
    assert toml.count("[allowlist]") == 1 and "rules" not in toml.split("[allowlist]")[1]
    assert "regexes" not in toml and "commits" not in toml and "stopwords" not in toml


def test_the_e2e_runner_creates_disposable_credentials_and_deletes_them():
    seed = (ROOT / "scripts" / "e2e_seed.py").read_text(encoding="utf-8")
    run = (ROOT / "apps" / "web" / "e2e" / "run.mjs").read_text(encoding="utf-8")
    assert 'name contains "e2e"' in seed or "e2e" in seed  # refuses non-disposable databases
    assert "rmSync(secrets)" in run  # secrets file removed in the runner's finally block
    assert "DROP DATABASE IF EXISTS asta_e2e" in run  # the users disappear with the database
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data/" in gi and "*.key" in gi and ".env" in gi
