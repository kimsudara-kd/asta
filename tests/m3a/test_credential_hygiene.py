"""M3A-R1 C: development credential hygiene.

No plaintext credential-like file may exist in the work tree except the single, documented, OBSOLETE
file below (its users were seeded into a disposable e2e database that no longer exists; removal is
pending the owner's confirmation - see docs/m3a/CREDENTIAL-HYGIENE.md). It is pinned by SHA-256 so
it cannot silently change, and any NEW file of this kind fails the test.
"""

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKIP = {"node_modules", ".venv", ".git", ".next"}
NAME = re.compile(r"(cred|secret|password|passwd|token|apikey|api_key|private)", re.IGNORECASE)
EXT = {".json", ".txt", ".env", ".yaml", ".yml", ".ini", ".cfg", ".key", ".pem"}

# SHA-256 prefix (16 hex chars = 64 bits) of the obsolete file; a prefix is enough to detect change
KNOWN_OBSOLETE = {"data/manual/creds.json": "9412ad3eb6fa6d76"}


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


def test_only_the_documented_obsolete_file_may_exist_and_it_must_be_unchanged():
    problems = []
    for p in credential_like_files():
        rel = p.relative_to(ROOT).as_posix()
        # source/documentation files that merely have such a word in their name are not credentials
        if rel.startswith(("src/", "tests/", "docs/", "scripts/", "infra/", "apps/web/src/")):
            continue
        want = KNOWN_OBSOLETE.get(rel)
        if want is None:
            problems.append(f"unexpected credential-like file: {rel}")
        elif not hashlib.sha256(p.read_bytes()).hexdigest().startswith(want):
            problems.append(f"documented obsolete file changed: {rel}")
    assert not problems, problems


def test_the_e2e_runner_creates_disposable_credentials_and_deletes_them():
    seed = (ROOT / "scripts" / "e2e_seed.py").read_text(encoding="utf-8")
    run = (ROOT / "apps" / "web" / "e2e" / "run.mjs").read_text(encoding="utf-8")
    assert 'name contains "e2e"' in seed or "e2e" in seed  # refuses non-disposable databases
    assert "rmSync(secrets)" in run  # secrets file removed in the runner's finally block
    assert "DROP DATABASE IF EXISTS asta_e2e" in run  # the users disappear with the database
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data/" in gi and "*.key" in gi and ".env" in gi
