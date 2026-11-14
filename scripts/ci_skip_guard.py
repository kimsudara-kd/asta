#!/usr/bin/env python3
"""Fail CI if any test was skipped for a reason that is not on the reviewed allow-list (M3-B-R5).

    python -m pytest tests -q -rs | tee pytest.log; python scripts/ci_skip_guard.py pytest.log

A security gate that silently skips (for example the Docker/firewall tests when Docker is missing) must be a RED
build, not a green one. The only reviewed skips are the opt-in network tests and the obsolete PowerShell parse.
"""

from __future__ import annotations

import re
import sys

ALLOWED = (
    "set ASTA_RUN_NETWORK_TESTS=1",  # opt-in public-network contract tests
    "PowerShell not available",  # obsolete Windows/Hyper-V parse
)


def main(path: str) -> int:
    bad = []
    for line in open(path, encoding="utf-8", errors="replace"):
        m = re.match(r"SKIPPED \[(\d+)\] (\S+): (.*)", line.strip())
        if m and not any(a in m.group(3) for a in ALLOWED):
            bad.append(f"{m.group(2)}: {m.group(3)} (x{m.group(1)})")
    if bad:
        print("UNREVIEWED SKIPS (a security gate did not run):")
        print("\n".join(" - " + b for b in bad))
        return 1
    print("skip guard: only reviewed skips")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
