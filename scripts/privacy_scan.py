#!/usr/bin/env python3
"""Privacy scan of git history against a PRIVATE list of real identifiers (M3-B-R6 workstream C).

    python3 scripts/privacy_scan.py --repo PATH --map FILE [--range A..B] [--include-public]

FILE is the private real<TAB>replacement map kept OUTSIDE the repository. For every identifier the scan reports
only the COUNT of commits (and short hashes) whose diffs add or remove it; it never prints the identifier or the
matching lines. Network addresses are scanned the same way: put the real LAN prefix, WAN address and tailnet name in
the map. (A generic private-range scan is deliberately not used: the design legitimately contains 10.77.x.x etc.) Exit 1 if anything is
found in the scanned range (default: the whole history), 0 otherwise.
"""

from __future__ import annotations

import subprocess
import sys


def git(repo: str, *a: str) -> str:
    return subprocess.run(
        ["git", "-C", repo, *a], capture_output=True, text=True, check=False
    ).stdout  # noqa: S603,S607


def main() -> int:
    argv = sys.argv[1:]
    repo = argv[argv.index("--repo") + 1]
    mapf = argv[argv.index("--map") + 1]
    rng = argv[argv.index("--range") + 1] if "--range" in argv else "--all"
    hits = 0
    for line in open(mapf, encoding="utf-8"):
        if "\t" not in line:
            continue
        real = line.split("\t", 1)[0]
        if not real or real == "P8":  # the product's own name is not an identifier
            continue
        commits = git(repo, "log", "-S" + real, "--format=%h", rng).split()
        if commits:
            hits += 1
            print(f"identifier #{hits}: {len(commits)} commit(s): {' '.join(commits[:6])}")
    print("PRIVACY SCAN:", "FINDINGS" if hits else "clean")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
