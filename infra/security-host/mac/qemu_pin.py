#!/usr/bin/env python3
"""Pin the QEMU binary and the Homebrew libraries it loads (M3-B-R4 workstream B).

    python3 qemu_pin.py generate [--out-dir DIR]   # owner, at provisioning, after checking the bottle
    python3 qemu_pin.py verify   [--pin FILE]      # read-only; exit 0 only when every file matches

Why: ASTA runs ``/opt/homebrew/bin/qemu-system-aarch64`` as the unprivileged ``_astavm``, but the
Homebrew tree is writable by the admin account, so a replaced binary or library would run inside the
security boundary's launcher. ``generate`` writes
  * ``qemu-pin.sha256``  - ``shasum -a 256 -c`` format: the binary, the firmware files and every
                           Homebrew dylib in its dependency closure (``otool -L``, recursively);
  * ``qemu-pin.json``    - metadata: formula version, tap, bottle tag and the bottle SHA-256 recorded
                           by Homebrew, signature facts (ad-hoc + hypervisor entitlement), generation time.
Provisioning installs ``qemu-pin.sha256`` root-owned next to the launcher and ``qemu-vms.sh run``
refuses to start a VM unless ``shasum -a 256 -c`` passes (fail closed). Homebrew re-signs the binary
ad hoc after relocation, so the installed hash is host-specific: trust comes from (1) the bottle SHA-256
that Homebrew verified at install time (``brew fetch --bottle-tag arm64_tahoe qemu`` re-checks it) and
(2) this pin, created once by the owner and checked before every boot. Stdlib only.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import subprocess
import sys
from pathlib import Path

BREW = Path("/opt/homebrew")
BINARY = BREW / "bin" / "qemu-system-aarch64"
FIRMWARE = [BREW / "share/qemu/edk2-aarch64-code.fd", BREW / "share/qemu/edk2-arm-vars.fd"]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(*args: str) -> str:
    r = subprocess.run(args, capture_output=True, text=True, check=False)  # noqa: S603
    return r.stdout + r.stderr  # codesign reports on stderr


def closure(binary: Path) -> list[Path]:
    """Homebrew-resident dylibs loaded (transitively) by the binary, resolved to real paths."""
    seen: dict[str, Path] = {}
    todo = [binary.resolve()]
    while todo:
        cur = todo.pop()
        for line in run("/usr/bin/otool", "-L", str(cur)).splitlines()[1:]:
            name = line.strip().split(" (")[0]
            if not name.startswith(str(BREW)):  # system libraries are protected by SIP
                continue
            real = Path(name).resolve()
            if str(real) not in seen and real.exists():
                seen[str(real)] = real
                todo.append(real)
    return sorted(seen.values())


def generate(out_dir: Path) -> None:
    real = BINARY.resolve()
    files = [real, *[f.resolve() for f in FIRMWARE if f.exists()], *closure(real)]
    lines = [f"{sha256(p)}  {p}" for p in dict.fromkeys(files)]
    (out_dir / "qemu-pin.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
    info = json.loads(run("/opt/homebrew/bin/brew", "info", "--json=v2", "qemu") or "{}")
    f = (info.get("formulae") or [{}])[0]
    files = (f.get("bottle", {}).get("stable", {}).get("files", {})).items()
    bottle = [{"tag": k, "sha256": v.get("sha256")} for k, v in files if "arm64" in k]
    meta = {
        "formula": "qemu",
        "version": (f.get("versions") or {}).get("stable"),
        "tap": f.get("tap"),
        "license": f.get("license"),
        "bottles": bottle,  # as recorded by Homebrew; verify with: brew fetch --bottle-tag arm64_tahoe qemu
        "binary": str(real),
        "binary_sha256": sha256(real),
        "signature": [
            ln.strip()
            for ln in run("/usr/bin/codesign", "-dv", str(real)).splitlines()
            if ln.startswith("Signature") or "flags" in ln
        ],
        "pinned_files": len(lines),
        "generated_utc": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
    }
    (out_dir / "qemu-pin.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    print(f"pinned {len(lines)} files; binary {meta['binary_sha256']}")


def verify(pin: Path) -> int:
    bad = 0
    for line in pin.read_text(encoding="utf-8").splitlines():
        want, _, path = line.partition("  ")
        p = Path(path)
        got = sha256(p) if p.exists() else "MISSING"
        if got != want:
            print(f"MISMATCH {path}: pinned {want[:12]}, found {got[:12]}")
            bad += 1
    print("qemu pin verified" if not bad else f"{bad} mismatch(es)")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["generate", "verify"])
    ap.add_argument("--out-dir", default=str(Path(__file__).parent))
    ap.add_argument("--pin", default=str(Path(__file__).parent / "qemu-pin.sha256"))
    a = ap.parse_args()
    if a.cmd == "generate":
        generate(Path(a.out_dir))
        return 0
    return verify(Path(a.pin))


if __name__ == "__main__":
    sys.exit(main())
