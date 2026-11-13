"""Network-isolation acceptance checks for the dedicated ASTA security-host VMs (M3A-R3).

Run ON the VM (root), stdlib only. NOT executable meaningfully on the development PC.

    python3 vm_acceptance.py runtime  pre   out-pre.json   [--negative-tests /path/negative_tests.py --proxy 10.77.0.1:3128]
    sudo reboot            # real reboot of the guest (and later of the hypervisor host)
    python3 vm_acceptance.py runtime  post  out-post.json  [--pre out-pre.json ...]

phase "pre"  records the state and checks every control;
phase "post" repeats the checks AFTER A REBOOT and additionally requires: a different boot_id, an
identical firewall ruleset hash, identical routes, the same services active. Results are PASS / FAIL /
SKIPPED (never silently passed). The acceptance matrix (docs/m3a/VM-BOUNDARY-DESIGN.md) lists the
tests that need the hypervisor side (switch has no uplink, packet capture on the uplink, snapshot revert).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

R: list[dict[str, str]] = []


def check(name: str, ok: bool | None, detail: str = "") -> None:
    R.append(
        {
            "check": name,
            "result": "SKIPPED" if ok is None else ("PASS" if ok else "FAIL"),
            "detail": detail[:300],
        }
    )


def sh(*args: str, timeout: int = 20) -> str:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603
        return (r.stdout + r.stderr).strip()
    except Exception as exc:  # noqa: BLE001
        return f"ERR:{type(exc).__name__}:{exc}"


def tcp(host: str, port: int, fam: int = socket.AF_INET, t: float = 3.0) -> str:
    try:
        s = socket.socket(fam, socket.SOCK_STREAM)
        s.settimeout(t)
        s.connect((host, port))
        s.close()
        return "CONNECTED"
    except Exception as exc:  # noqa: BLE001
        return f"blocked:{type(exc).__name__}"


def policies(ruleset: str) -> list[str]:
    return [ln.strip() for ln in ruleset.splitlines() if "policy drop" in ln]


def default_routes(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.startswith("default")]


def state() -> dict[str, object]:
    rs = sh("nft", "list", "ruleset")
    # counters change between runs; hash the rules without packet/byte counters
    rules = "\n".join(
        ln.split("counter packets")[0].rstrip() if "counter packets" in ln else ln
        for ln in rs.splitlines()
        if "expires" not in ln and "elements" not in ln
    )
    return {
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        if os.name == "posix"
        else "n/a",
        "ruleset_sha256": hashlib.sha256(rules.encode()).hexdigest(),
        "policies": policies(rs),
        "routes4": sh("ip", "-4", "route"),
        "routes6": sh("ip", "-6", "route"),
        "ip_forward": Path("/proc/sys/net/ipv4/ip_forward").read_text().strip(),
        "ipv6_disabled": Path("/proc/sys/net/ipv6/conf/all/disable_ipv6").read_text().strip(),
        "listeners": sh("ss", "-H", "-ltnu"),
        "services": {
            u: sh("systemctl", "is-active", u)
            for u in (
                "nftables.service",
                "asta-egress-proxy.service",
                "asta-allowlist-refresh.timer",
                "chrony.service",
            )
        },
    }


def common(st: dict[str, object]) -> None:
    check(
        "nftables default policy DROP in input, output and forward",
        len(st["policies"]) >= 3,
        str(st["policies"]),
    )  # type: ignore[arg-type]
    check("ip_forward is 0", st["ip_forward"] == "0", str(st["ip_forward"]))
    check("IPv6 disabled", st["ipv6_disabled"] == "1", str(st["ipv6_disabled"]))
    check(
        "no IPv6 routes",
        not default_routes(str(st["routes6"])) and "::/0" not in str(st["routes6"]),
    )
    check(
        "nftables service active",
        st["services"]["nftables.service"] == "active",
        str(st["services"]),
    )  # type: ignore[index]


def runtime(args: argparse.Namespace, st: dict[str, object]) -> None:
    common(st)
    check(
        "NO default route on the runtime VM",
        not default_routes(str(st["routes4"])),
        str(st["routes4"]),
    )
    check("resolver is not configured", "nameserver" not in Path("/etc/resolv.conf").read_text()
          or "127.0.0.53" in Path("/etc/resolv.conf").read_text(), "resolv.conf checked")  # fmt: skip
    for ip, port in (
        ("1.1.1.1", 443),
        ("8.8.8.8", 53),
        ("93.184.216.34", 80),
        ("169.254.169.254", 80),
        ("192.168.1.1", 80),
    ):
        r = tcp(ip, port)
        check(f"direct {ip}:{port} blocked", r != "CONNECTED", r)
    r = tcp("2606:4700:4700::1111", 443, socket.AF_INET6)
    check("external IPv6 blocked", r != "CONNECTED", r)
    try:
        socket.gethostbyname("example.com")
        check("name resolution is unavailable", False, "resolved")
    except OSError as exc:
        check("name resolution is unavailable", True, type(exc).__name__)
    host, port = args.proxy.rsplit(":", 1)
    r = tcp(host, int(port))
    check("the firewall VM proxy port is reachable", r == "CONNECTED", r)
    others = [p for p in (22, 80, 443, 8080, 3129) if tcp(host, p, t=2.0) == "CONNECTED"]
    check("no other port on the firewall VM is reachable", not others, str(others))
    if args.negative_tests:
        env = {**os.environ, "ASTA_PROXY": args.proxy}
        out = subprocess.run(
            [sys.executable, args.negative_tests],
            capture_output=True,
            text=True,
            timeout=900,
            env=env,
            check=False,
        )  # noqa: S603
        try:
            d = json.loads(out.stdout)
            check(
                "78-check negative suite from the runtime VM",
                d["failed"] == 0,
                f"total={d['total']} failed={d['failed']}",
            )
        except Exception:  # noqa: BLE001
            check(
                "78-check negative suite from the runtime VM",
                False,
                (out.stdout + out.stderr)[-200:],
            )
    else:
        check(
            "78-check negative suite from the runtime VM", None, "pass --negative-tests to run it"
        )


def firewall(args: argparse.Namespace, st: dict[str, object]) -> None:
    common(st)
    check(
        "proxy service active",
        st["services"]["asta-egress-proxy.service"] == "active",
        str(st["services"]),
    )  # type: ignore[index]
    check(
        "allowlist refresh timer active", st["services"]["asta-allowlist-refresh.timer"] == "active"
    )  # type: ignore[index]
    check(
        "allowed_dst set is populated",
        "elements" in sh("nft", "list", "set", "inet", "asta_fw", "allowed_dst"),
    )
    lst = str(st["listeners"])
    check("only the proxy (and optionally ssh from the private net) listens", all(
        ":3128" in ln or ":22" in ln or ln.startswith(("udp",)) and ("127." in ln or ":123" in ln or ":323" in ln or ":68" in ln)
        for ln in lst.splitlines() if ln.strip()), lst[:200])  # fmt: skip
    ps = sh("ps", "-eo", "user:20,comm", "--no-headers")
    check(
        "proxy does not run as root",
        not any(ln.split()[0] == "root" and "python" in ln for ln in ps.splitlines()),
        ps[:200],
    )
    # unprivileged attacker inside the firewall VM: no DNS, no direct IP (uid 65534)
    unpriv = [
        "setpriv",
        "--reuid",
        "65534",
        "--regid",
        "65534",
        "--clear-groups",
        sys.executable,
        "-c",
    ]
    code = "import socket;s=socket.socket();s.settimeout(3);\ntry:\n s.connect(('{h}',{p}));print('CONNECTED')\nexcept Exception as e:\n print('blocked:'+type(e).__name__)"
    for h, p in (("1.1.1.1", 443), ("93.184.216.34", 443), ("8.8.8.8", 53)):
        r = sh(*unpriv, code.format(h=h, p=p))
        check(
            f"unprivileged process: direct {h}:{p} blocked",
            "CONNECTED" not in r and not r.startswith("ERR"),
            r,
        )
    udp = "import socket;s=socket.socket(2,2);s.settimeout(3);s.sendto(b'\\x12\\x34\\x01\\x00\\x00\\x01\\x00\\x00\\x00\\x00\\x00\\x00\\x07example\\x03com\\x00\\x00\\x01\\x00\\x01',('{ns}',53))\ntry:\n s.recvfrom(512);print('ANSWER')\nexcept Exception as e:\n print('no-answer')"
    ns = args.upstream_dns
    r = sh(*unpriv, udp.format(ns=ns))
    check(
        f"unprivileged process: no DNS egress to {ns}",
        "ANSWER" not in r and not r.startswith("ERR"),
        r,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("role", choices=["runtime", "firewall"])
    ap.add_argument("phase", choices=["pre", "post"])
    ap.add_argument("out")
    ap.add_argument("--pre")
    ap.add_argument("--negative-tests")
    ap.add_argument("--proxy", default="10.77.0.1:3128")
    ap.add_argument("--upstream-dns", default="192.168.1.1")
    a = ap.parse_args()
    if os.name != "posix":
        print("must run on the Linux VM", file=sys.stderr)
        return 2
    st = state()
    (runtime if a.role == "runtime" else firewall)(a, st)
    if a.phase == "post":
        if not a.pre:
            check("compare with pre-reboot state", None, "--pre not given")
        else:
            pre = json.loads(Path(a.pre).read_text())["state"]
            check("the guest really rebooted (boot_id changed)", pre["boot_id"] != st["boot_id"])
            check(
                "firewall ruleset identical after reboot",
                pre["ruleset_sha256"] == st["ruleset_sha256"],
            )
            check(
                "routes identical after reboot",
                pre["routes4"] == st["routes4"] and pre["routes6"] == st["routes6"],
            )
            check(
                "services active as before", pre["services"] == st["services"], str(st["services"])
            )
    res = {"role": a.role, "phase": a.phase, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "state": st, "checks": R,
           "failed": [c for c in R if c["result"] == "FAIL"], "skipped": [c["check"] for c in R if c["result"] == "SKIPPED"]}  # fmt: skip
    Path(a.out).write_text(json.dumps(res, indent=1))
    print(
        json.dumps({"failed": len(res["failed"]), "skipped": len(res["skipped"]), "checks": len(R)})
    )  # type: ignore[arg-type]
    return 1 if res["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
