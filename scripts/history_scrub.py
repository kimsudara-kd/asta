#!/usr/bin/env python3
"""Replace private identifiers in the files a commit CHANGED, for use inside `git filter-branch --tree-filter`
on a SCRATCH CLONE (M3-B-R6 workstream C). It never runs against the real repository by itself.

    BASE=<last already-public commit> python3 scripts/history_scrub.py --map /path/to/private/replace-map.tsv

The map (real<TAB>replacement per line) lives OUTSIDE the repository because it contains the real names. Only
tracked text files whose content differs from the BASE version are touched: anything already public at BASE is
left exactly as it is (rewriting public content would change published history). Exit 2 if a map entry is empty.
"""

from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    argv = sys.argv[1:]
    if "--map" not in argv:
        print("usage: history_scrub.py --map FILE (env BASE=<commit>)", file=sys.stderr)
        return 2
    base = os.environ["BASE"]
    pairs = []
    for line in open(argv[argv.index("--map") + 1], encoding="utf-8"):
        if "\t" in line.rstrip("\n"):
            a, b = line.rstrip("\n").split("\t", 1)
            if not a:
                return 2
            pairs.append((a, b))
    pairs.sort(key=lambda p: -len(p[0]))  # longest first
    files = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True).stdout.split(
        b"\0"
    )  # noqa: S603,S607
    for raw in files:
        if not raw:
            continue
        path = raw.decode()
        try:
            data = open(path, "rb").read()
            text = data.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # binary or missing: untouched
        new = text
        for a, b in pairs:
            new = new.replace(a, b)
        if new == text:
            continue
        shown = subprocess.run(["git", "show", f"{base}:{path}"], capture_output=True)  # noqa: S603,S607
        if shown.returncode == 0 and shown.stdout == data:
            continue  # identical to the public version: leave public content alone
        open(path, "w", encoding="utf-8", newline="").write(new)
    return 0


if __name__ == "__main__":
    sys.exit(main())
