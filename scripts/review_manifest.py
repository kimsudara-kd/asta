"""Review-package manifest (M3A-R2 Workstream D).

    python scripts/review_manifest.py --write    # snapshot SHA-256 of every review artifact
    python scripts/review_manifest.py --verify   # compare the working tree with the snapshot

The manifest ``docs/evidence/m3a/REVIEW-MANIFEST.json`` lets an independent reviewer prove that the
files they review are the files that were delivered (and the commit they were generated from).
Text files are hashed with CRLF normalised (git autocrlf on Windows).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "evidence" / "m3a" / "REVIEW-MANIFEST.json"
SCOPES = [
    "docs/m3a",
    "docs/evidence/m3a",
    "docs/evidence/M3A-acceptance.md",
    "docs/evidence/FROZEN-EVIDENCE.json",
    "docs/decisions",
    "infra/egress",
    "infra/security-host",
    "src/asta/security",
    "tests/m3a",
    "scripts/verify_frozen_evidence.py",
    "scripts/review_manifest.py",
    ".gitleaksignore",
    ".gitleaks.toml",
    # M3-B-R3: the order-path work, the roles/migrations and the R-series evidence
    "docs/m3b",
    "docs/evidence/M3B-GOVERNANCE-01.md",
    "docs/evidence/M3B-DECISION-007.md",
    "docs/evidence/M3B-R2.md",
    "docs/evidence/M3B-R3.md",
    "docs/evidence/M3B-R4.md",
    "docs/evidence/M3B-R5.md",
    ".github/workflows/ci.yml",
    "scripts/ci_skip_guard.py",
    "scripts/docker_pg_volume_verify.py",
    "src/asta/orders",
    "src/asta/core/order_gate.py",
    "migrations/versions",
    "tests/orders",
    "tests/integration/test_m3b_integrity.py",
    "tests/integration/test_risk_bridge.py",
    "tests/integration/test_bridge_supervision.py",
    "scripts/secret_scan.py",
]


def digest(p: Path) -> str:
    data = p.read_bytes()
    if p.suffix not in (".png", ".bin"):
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def files() -> list[Path]:
    out: list[Path] = []
    for s in SCOPES:
        p = ROOT / s
        if p.is_file():
            out.append(p)
        elif p.is_dir():
            out += [f for f in p.rglob("*") if f.is_file() and "__pycache__" not in f.parts]
    return sorted(set(out) - {MANIFEST})


def head() -> str:
    r = subprocess.run(  # noqa: S603
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return r.stdout.strip()


def build() -> dict[str, object]:
    return {
        "generated_from_commit": head(),
        "note": "SHA-256 of review artifacts; text normalised to LF. Regenerate only deliberately.",
        "files": [{"path": f.relative_to(ROOT).as_posix(), "sha256": digest(f)} for f in files()],
    }


def main() -> int:
    if "--write" in sys.argv:
        MANIFEST.write_text(json.dumps(build(), indent=1) + "\n", encoding="utf-8")
        print(f"wrote {MANIFEST}")
        return 0
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    problems = []
    pinned: dict[str, str] = {e["path"]: e["sha256"] for e in m["files"]}
    for rel, want in pinned.items():
        f = ROOT / rel
        if not f.exists():
            problems.append(f"missing {rel}")
        elif digest(f) != want:
            problems.append(f"modified {rel}")
    for f in files():
        rel = f.relative_to(ROOT).as_posix()
        if rel not in pinned:
            problems.append(f"not in manifest {rel}")
    for p in problems:
        print("REVIEW-MANIFEST:", p)
    print("review manifest verified" if not problems else f"{len(problems)} difference(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
