"""Dependency-free secret scan over tracked files AND full git history.

Exit 1 on any finding. Patterns are deliberately conservative; the CI also runs gitleaks.
"""

from __future__ import annotations

import re
import subprocess
import sys

PATTERNS = {
    "private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "aws access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    "binance-style key": re.compile(r"\b[A-Za-z0-9]{64}\b(?=.*(api|secret|key))", re.I),
    "assigned secret": re.compile(
        r"(?i)\b(api[_-]?key|api[_-]?secret|secret[_-]?key|passphrase)\b\s*[:=]\s*['\"][A-Za-z0-9+/_\-]{16,}['\"]"
    ),
}
ALLOW = ("test-only", "asta_dev_only", "asta_test_only", "change-me")


def git(*args: str) -> str:
    # fixed argv, no shell, no external input
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8", errors="replace")  # history may contain binary (PNG) diffs


def scan(label: str, text: str, findings: list[str]) -> None:
    for i, line in enumerate(text.splitlines(), 1):
        if any(a in line for a in ALLOW):
            continue
        for name, pat in PATTERNS.items():
            if pat.search(line):
                findings.append(f"{label}:{i}: {name}")


def main() -> int:
    findings: list[str] = []
    for path in git("ls-files").splitlines():
        if path.endswith((".lock", "pnpm-lock.yaml", ".png", ".ico")):
            continue
        try:
            scan(path, open(path, encoding="utf-8", errors="ignore").read(), findings)
        except OSError:
            pass
    # full history: every added line in every commit
    scan(
        "history",
        git("log", "-p", "--all", "--no-color", "--no-textconv", "--format=commit %H"),
        findings,
    )
    for f in findings:
        print("FINDING", f)
    print(f"secret scan: {len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
