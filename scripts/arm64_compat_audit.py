"""ARM64 compatibility audit of the locked Python dependencies (M3A-MAC-01). Read-only.

    python scripts/arm64_compat_audit.py [uv.lock] [out.json]

Reads ``uv.lock`` (no network, no installation) and, for every locked package, determines from the
recorded wheel file names whether it can be installed WITHOUT compiling on:
  * Linux ARM64 (aarch64), CPython 3.12  - the planned VM guest (manylinux/musllinux aarch64 wheel,
    or a pure-Python ``py3-none-any`` wheel),
  * macOS Apple Silicon (arm64)          - for development on the Mac host if ever needed.
Packages with only an sdist, or without a matching wheel, are reported as NEEDS-BUILD.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

LINUX_ARM = re.compile(r"(many|musl)?linux[\w.]*_aarch64")
MAC_ARM = re.compile(r"macosx_[\d_]+_(arm64|universal2)")
PY312 = re.compile(r"-(py3|py2\.py3|cp312|cp3|abi3)")


def classify(wheels: list[str]) -> tuple[bool, bool, str]:
    pure = any(w.endswith("-none-any.whl") for w in wheels)
    linux = pure or any(
        LINUX_ARM.search(w) and ("cp312" in w or "abi3" in w or "cp3" in w or "-py3-none-" in w)
        for w in wheels
    )
    mac = pure or any(
        MAC_ARM.search(w) and ("cp312" in w or "abi3" in w or "cp3" in w or "-py3-none-" in w)
        for w in wheels
    )
    kind = "pure-python" if pure else ("native wheel" if (linux or mac) else "sdist-only/none")
    return linux, mac, kind


def main() -> int:
    lock = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("uv.lock")
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("arm64_compat.json")
    data = tomllib.loads(lock.read_text(encoding="utf-8"))
    rows = []
    for p in data["package"]:
        name = p["name"]
        if name == "asta":
            continue
        wheels = [w["url"].rsplit("/", 1)[-1] for w in p.get("wheels", [])]
        has_sdist = "sdist" in p
        linux, mac, kind = classify(wheels)
        rows.append(
            {
                "package": name,
                "version": p.get("version"),
                "kind": kind,
                "linux_aarch64_no_build": linux,
                "macos_arm64_no_build": mac,
                "sdist_available": has_sdist,
            }
        )
    need_linux = [r["package"] for r in rows if not r["linux_aarch64_no_build"]]
    need_mac = [r["package"] for r in rows if not r["macos_arm64_no_build"]]
    res = {
        "packages": len(rows),
        "pure_python": sum(r["kind"] == "pure-python" for r in rows),
        "native_with_arm64_wheel": sum(r["kind"] == "native wheel" for r in rows),
        "needs_build_on_linux_aarch64": need_linux,
        "needs_build_on_macos_arm64": need_mac,
        "rows": rows,
    }
    out.write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k != "rows"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
