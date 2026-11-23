#!/usr/bin/env python3
"""Unprivileged macOS host-posture audit (M3-B-FINAL phase 3). Read-only; prints JSON; no addresses or names.

    python3 scripts/host_posture.py [--out FILE] [--require-dedicated]

Reports the ACTUAL state of: FileVault, application firewall (+stealth), automatic login, Remote Login (sshd),
SMB, listening TCP services by bind scope (loopback vs all-interfaces) and process, Docker Desktop, VM-runtime
tools. With --require-dedicated exits 1 when any dedicated-host control is not in its required state.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys

FW = "/usr/libexec/ApplicationFirewall/socketfilterfw"


def run(*cmd: str) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)  # noqa: S603
        return (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"unavailable: {type(exc).__name__}"


def port_open(port: int) -> bool:
    import socket

    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def firewall_on(text: str) -> bool:
    """socketfilterfw --getglobalstate: State = 1 (on) or 2 (block all incoming). Anything unparseable is OFF."""
    m = re.search(r"\(State = ([0-9])\)", text)
    if m is not None:
        return m.group(1) in ("1", "2")
    return "enabled" in text.lower() and "disabled" not in text.lower()


def listeners() -> list[dict[str, str]]:
    out = []
    seen = set()
    for ln in run("/usr/sbin/lsof", "-nP", "-iTCP", "-sTCP:LISTEN").splitlines()[1:]:
        f = ln.split()
        if len(f) < 9:
            continue
        addr = f[8]
        host, _, port = addr.rpartition(":")
        scope = "loopback" if host in ("127.0.0.1", "[::1]", "localhost") else "ALL-INTERFACES"
        key = (f[0], port, scope)
        if key not in seen:
            seen.add(key)
            out.append({"process": f[0].replace("\\x20", " "), "port": port, "scope": scope})
    return sorted(out, key=lambda d: (d["scope"], int(d["port"]) if d["port"].isdigit() else 0))


def posture() -> dict[str, object]:
    fv = run("/usr/bin/fdesetup", "status")
    fw_state = run(FW, "--getglobalstate")
    stealth = run(FW, "--getstealthmode")
    auto = run(
        "/usr/bin/defaults", "read", "/Library/Preferences/com.apple.loginwindow", "autoLoginUser"
    )
    ls = listeners()
    # lsof without root cannot see root-owned listeners (sshd, smbd): probe the well-known ports directly
    probed = {n: port_open(n) for n in (22, 445, 139, 5900, 3283)}
    all_if = [x for x in ls if x["scope"] == "ALL-INTERFACES"]
    docker = {
        "docker_cli": bool(shutil.which("docker")),
        "docker_desktop_running": "com.docker" in run("/bin/ps", "-axo", "comm="),
    }
    return {
        "filevault_on": "On" in fv and "Off" not in fv,
        "app_firewall_on": firewall_on(fw_state),
        "stealth_on": "enabled" in stealth.lower(),
        "auto_login_disabled": auto.startswith("The domain/default pair")
        or "does not exist" in auto,
        "ssh_listening": probed[22],
        "smb_listening": probed[445] or probed[139],
        "screen_sharing_or_ard": probed[5900] or probed[3283],
        "all_interface_listeners": all_if,
        "docker": docker,
        "vm_tools": {
            t: bool(shutil.which(t)) for t in ("qemu-system-aarch64", "socket_vmnet", "limactl")
        },
        "python": sys.version.split()[0],
    }


REQUIRED = {
    "filevault_on": True,
    "app_firewall_on": True,
    "auto_login_disabled": True,
    "ssh_listening": False,  # unless key-only + restricted (reviewed exception)
    "smb_listening": False,
    "screen_sharing_or_ard": False,
}


def main() -> int:
    p = posture()
    gaps = [k for k, want in REQUIRED.items() if p[k] != want]
    if p["all_interface_listeners"]:
        gaps.append("all_interface_listeners")
    if p["docker"]["docker_desktop_running"]:  # type: ignore[index]
        gaps.append("unrelated_docker_workloads_present")
    p["dedicated_host_gaps"] = gaps
    text = json.dumps(p, indent=1)
    if "--out" in sys.argv:
        open(sys.argv[sys.argv.index("--out") + 1], "w", encoding="utf-8").write(text + "\n")
    print(text)
    return 1 if ("--require-dedicated" in sys.argv and gaps) else 0


if __name__ == "__main__":
    sys.exit(main())
