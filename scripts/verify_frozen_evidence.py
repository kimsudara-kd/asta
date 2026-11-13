"""Frozen accepted-evidence integrity (ASTA-M3A-R1, workstream D). Read-only except ``--write``.

    python scripts/verify_frozen_evidence.py            # verify; exit 1 on any difference
    python scripts/verify_frozen_evidence.py --write    # (re)generate the manifest - a deliberate,
                                                        # reviewed act, never part of a test run

The manifest ``docs/evidence/FROZEN-EVIDENCE.json`` pins, for the M0, M1 and M2 acceptance tags,
the tag's commit and the SHA-256 of every accepted evidence file. Verification checks that
(1) each tag still points at the pinned commit, (2) every working-tree file equals the pinned hash
AND the blob stored at the tag, (3) no file was added to or removed from a frozen directory, and
(4) HEAD equals the tag for the frozen paths. Text files are compared with CRLF normalised
(git autocrlf on Windows); binary files (PNG) byte-exact.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "evidence" / "FROZEN-EVIDENCE.json"
E = "docs/evidence"
FROZEN: dict[str, dict[str, object]] = {
    "M0": {
        "tag": "m0-accepted-with-residual",
        "dirs": [f"{E}/m0-r1"],
        "files": [f"{E}/M0-ACCEPTANCE-RECORD.md", f"{E}/M0-acceptance.md"],
    },
    "M1": {
        "tag": "m1-accepted-with-residual",
        "dirs": [f"{E}/m1", f"{E}/m1-r1"],
        "files": [f"{E}/M1-ACCEPTANCE-RECORD.md", f"{E}/M1-acceptance.md"],
    },
    "M2": {
        "tag": "m2-accepted-with-residual",
        "dirs": [f"{E}/m2"],
        "files": [f"{E}/M2-ACCEPTANCE-RECORD.md", f"{E}/M2-acceptance.md"],
    },
}


def git(*args: str) -> bytes:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, check=True).stdout  # noqa: S603,S607


def norm(path: str, data: bytes) -> bytes:
    return data if path.endswith(".png") else data.replace(b"\r\n", b"\n")


def sha(path: str, data: bytes) -> str:
    return hashlib.sha256(norm(path, data)).hexdigest()


def tag_commit(tag: str) -> str:
    return git("rev-parse", f"{tag}^{{commit}}").decode().strip()


def tag_files(commit: str, spec: dict[str, object]) -> list[str]:
    out: list[str] = []
    for d in spec["dirs"]:  # type: ignore[attr-defined]
        out += git("ls-tree", "-r", "--name-only", commit, "--", str(d)).decode().split()
    out += list(spec["files"])  # type: ignore[call-overload]
    return sorted(set(out))


def build() -> dict[str, object]:
    m: dict[str, object] = {
        "note": "Frozen accepted evidence. Do not edit by hand.",
        "milestones": {},
    }
    for name, spec in FROZEN.items():
        commit = tag_commit(str(spec["tag"]))
        files = {p: sha(p, git("show", f"{commit}:{p}")) for p in tag_files(commit, spec)}
        # Evidence files that were never in the tag (the *.log files were git-ignored and so only
        # existed in the working tree): pinned by the hash of the working-tree file at pin time.
        late: dict[str, str] = {}
        for d in spec["dirs"]:  # type: ignore[attr-defined]
            for f in sorted((ROOT / str(d)).rglob("*")):
                rel = f.relative_to(ROOT).as_posix()
                if f.is_file() and rel not in files:
                    late[rel] = sha(rel, f.read_bytes())
        m["milestones"][name] = {  # type: ignore[index]
            "tag": spec["tag"],
            "tag_commit": commit,
            "files": files,
            "files_not_in_tag": late,
        }
    return m


def verify() -> list[str]:
    problems: list[str] = []
    if not MANIFEST.exists():
        return [f"manifest missing: {MANIFEST}"]
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for name, spec in FROZEN.items():
        entry = manifest["milestones"].get(name)
        if entry is None:
            problems.append(f"{name}: not in manifest")
            continue
        try:
            commit = tag_commit(str(spec["tag"]))
        except subprocess.CalledProcessError:
            problems.append(f"{name}: tag {spec['tag']} is missing")
            continue
        if commit != entry["tag_commit"]:
            problems.append(
                f"{name}: tag {spec['tag']} moved {entry['tag_commit'][:8]} -> {commit[:8]}"
            )
            continue
        pinned: dict[str, str] = entry["files"]
        late: dict[str, str] = entry.get("files_not_in_tag", {})
        for p, want in pinned.items():
            f = ROOT / p
            if not f.exists():
                problems.append(f"{name}: missing {p}")
                continue
            if sha(p, f.read_bytes()) != want:
                problems.append(f"{name}: MODIFIED working-tree file {p}")
            if sha(p, git("show", f"HEAD:{p}")) != want:
                problems.append(f"{name}: HEAD differs from tag for {p}")
        for p, want in late.items():
            f = ROOT / p
            if not f.exists():
                problems.append(f"{name}: missing (not-in-tag) {p}")
            elif sha(p, f.read_bytes()) != want:
                problems.append(f"{name}: MODIFIED working-tree file {p}")
            try:
                if sha(p, git("show", f"HEAD:{p}")) != want:
                    problems.append(f"{name}: HEAD differs from pinned hash for {p}")
            except subprocess.CalledProcessError:
                problems.append(f"{name}: {p} is not tracked in git (preservation risk)")
        for d in spec["dirs"]:  # type: ignore[attr-defined]
            base = ROOT / str(d)
            for f in base.rglob("*") if base.exists() else []:
                rel = f.relative_to(ROOT).as_posix()
                if f.is_file() and rel not in pinned and rel not in late:
                    problems.append(f"{name}: UNEXPECTED file in frozen directory {rel}")
    return problems


def main() -> int:
    if "--write" in sys.argv:
        MANIFEST.write_text(json.dumps(build(), indent=1) + "\n", encoding="utf-8")
        print(f"wrote {MANIFEST}")
        return 0
    problems = verify()
    for p in problems:
        print("FROZEN-EVIDENCE:", p)
    print("frozen evidence intact" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
