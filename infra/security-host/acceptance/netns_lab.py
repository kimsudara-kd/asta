"""Enforcement lab for the Mac-mini three-VM firewall rule sets, using Linux network namespaces (M3A-MAC-01).

Run as root INSIDE a disposable Linux VM (never on a workstation or the Mac host):

    sudo python3 netns_lab.py <repo>/infra/security-host/mac out.json [--internet-ip 1.1.1.1]

It builds the topology the rule sets expect - namespaces asta-fw / asta-app / asta-cred joined by veth
pairs named exactly as in the rules (app: fw0, cred: fw0, fw: app0 / cred0 / up0), loads the three
UNMODIFIED nftables files (only UPSTREAM_DNS is pointed at the lab gateway), starts listeners, then
sends real packets and records PASS/FAIL against the expected outcome. It proves the RULES, not the
Mac host, the hypervisor or the unix-socket links (those need the real deployment). Everything it creates
is removed at the end; nothing leaves the VM except the fw namespace's allowlisted test connection.

Stdlib only. Exit code 0 only if every check passed.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

NS = ("asta-fw", "asta-app", "asta-cred")
LAB_GW = (
    "10.99.0.1"  # the VM's own namespace: lab 'router' (NAT to the VM's real uplink) + fake DNS
)
LAB_FW_UP = "10.99.0.2"
R: list[dict[str, str]] = []


def sh(cmd: str, check: bool = True, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S602  (lab tool: fixed strings built here, run as root in a throw-away VM)
        cmd, shell=True, capture_output=True, text=True, timeout=timeout, check=check
    )


def nsx(
    ns: str, cmd: str, check: bool = True, timeout: int = 20
) -> subprocess.CompletedProcess[str]:
    return sh(f"ip netns exec {ns} {cmd}", check=check, timeout=timeout)


def record(name: str, expected: str, ok: bool, detail: str = "") -> None:
    R.append(
        {
            "check": name,
            "expected": expected,
            "result": "PASS" if ok else "FAIL",
            "detail": detail[:200],
        }
    )
    print(f"{'PASS' if ok else 'FAIL'}  {name}  (expected {expected})  {detail[:120]}")


CONNECT = (
    "import socket,sys;s=socket.socket();s.settimeout(3)\n"
    "try:\n s.connect((sys.argv[1],int(sys.argv[2])));print('CONNECTED')\n"
    "except Exception as e:\n print('FAILED',type(e).__name__)\n"
)


def can_connect(ns: str, ip: str, port: int, user: str | None = None) -> bool:
    prefix = f"setpriv --reuid={user} --regid={user} --clear-groups " if user else ""
    out = nsx(ns, f'{prefix}python3 -c "{CONNECT}" {ip} {port}', check=False, timeout=15).stdout
    return "CONNECTED" in out


def listen(ns: str, ip: str, port: int) -> None:
    code = (
        "import socket,time;s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);"
        f"s.bind(('{ip}',{port}));s.listen(8)\nwhile True:\n c,_=s.accept();c.close()"
    )
    sh(f'ip netns exec {ns} nohup python3 -c "{code}" >/dev/null 2>&1 &', check=False)


def udp_probe(ns: str, dst: str, user: str | None = None) -> bool:
    """Send a UDP datagram to dst:53 from ns; True when the lab's fake DNS (in the default ns) gets it."""
    prefix = f"setpriv --reuid={user} --regid={user} --clear-groups " if user else ""
    token = f"probe-{ns}-{user or 'root'}-{time.time_ns()}"
    log = Path(tempfile.gettempdir()) / "lab-udp.log"
    log.unlink(missing_ok=True)
    recv_code = (
        "import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.settimeout(6);"
        f"s.bind(('{dst}',53));d,_=s.recvfrom(100);open('{log}','wb').write(d)"
    )
    recv = subprocess.Popen(  # noqa: S603  (lab: fixed argv)
        ["python3", "-c", recv_code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    time.sleep(1)  # the receiver must be bound BEFORE the probe is sent
    nsx(
        ns,
        f'{prefix}python3 -c "import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);'
        f"s.sendto(b'{token}',('{dst}',53))\"",
        check=False,
    )
    try:
        recv.wait(timeout=8)
    except subprocess.TimeoutExpired:
        recv.kill()
    return log.exists() and token in log.read_text(errors="replace")


def teardown() -> None:
    sh("pkill -f 'ip netns exec asta-' ", check=False)
    for ns in NS:
        sh(f"ip netns pids {ns} 2>/dev/null | xargs -r kill", check=False)
        sh(f"ip netns del {ns}", check=False)
    sh("ip link del labup", check=False)
    sh("iptables -t nat -D POSTROUTING -s 10.99.0.0/30 -j MASQUERADE", check=False)


def build(mac_dir: Path) -> None:
    teardown()
    for ns in NS:
        sh(f"ip netns add {ns}")
        nsx(ns, "ip link set lo up")
    # app <-> fw
    sh("ip link add fw0 type veth peer name app0")
    sh("ip link set fw0 netns asta-app")
    sh("ip link set app0 netns asta-fw")
    nsx("asta-app", "ip addr add 10.77.1.2/30 dev fw0")
    nsx("asta-app", "ip link set fw0 up")
    nsx("asta-fw", "ip addr add 10.77.1.1/30 dev app0")
    nsx("asta-fw", "ip link set app0 up")
    # cred <-> fw
    sh("ip link add fw0c type veth peer name cred0")
    sh("ip link set fw0c netns asta-cred")
    nsx("asta-cred", "ip link set fw0c name fw0")
    sh("ip link set cred0 netns asta-fw")
    nsx("asta-cred", "ip addr add 10.77.2.2/30 dev fw0")
    nsx("asta-cred", "ip link set fw0 up")
    nsx("asta-fw", "ip addr add 10.77.2.1/30 dev cred0")
    nsx("asta-fw", "ip link set cred0 up")
    # uplink: fw up0 <-> lab gateway in the VM's own namespace (NAT to the VM's real network)
    sh("ip link add labup type veth peer name up0")
    sh("ip link set up0 netns asta-fw")
    sh(f"ip addr add {LAB_GW}/30 dev labup && ip link set labup up")
    nsx("asta-fw", f"ip addr add {LAB_FW_UP}/30 dev up0")
    nsx("asta-fw", "ip link set up0 up")
    nsx("asta-fw", f"ip route add default via {LAB_GW}")
    sh("sysctl -qw net.ipv4.ip_forward=1")
    sh("iptables -t nat -A POSTROUTING -s 10.99.0.0/30 -j MASQUERADE", check=False)
    # WORST CASE for the 'never forwards' claim: forwarding enabled in asta-fw and default routes via it
    nsx("asta-fw", "sysctl -qw net.ipv4.ip_forward=1")
    nsx("asta-app", "ip route add default via 10.77.1.1")
    nsx("asta-cred", "ip route add default via 10.77.2.1")
    for ns, name in (("asta-fw", "firewall"), ("asta-app", "app"), ("asta-cred", "cred")):
        text = (mac_dir / f"{name}-vm" / "nftables.conf").read_text()
        text = text.replace(
            "define UPSTREAM_DNS = { 192.168.1.1 }", f"define UPSTREAM_DNS = {{ {LAB_GW} }}"
        )
        (Path(tempfile.gettempdir()) / f"lab-{name}.nft").write_text(text)
        nsx(ns, f"nft -f {Path(tempfile.gettempdir()) / f'lab-{name}.nft'}")


def main() -> int:
    mac_dir = Path(sys.argv[1])
    out = Path(sys.argv[2])
    inet_ip = (
        sys.argv[sys.argv.index("--internet-ip") + 1] if "--internet-ip" in sys.argv else "1.1.1.1"
    )
    build(mac_dir)
    try:
        listen("asta-fw", "10.77.1.1", 3128)
        listen("asta-fw", "10.77.2.1", 3128)
        listen("asta-fw", "10.77.1.1", 8080)  # a service that exists but must stay unreachable
        listen("asta-app", "10.77.1.2", 5432)  # inbound targets: exist, must stay unreachable
        listen("asta-cred", "10.77.2.2", 7443)
        time.sleep(1)

        # ---- asta-app (paper service VM)
        record(
            "app -> fw proxy 10.77.1.1:3128", "allowed", can_connect("asta-app", "10.77.1.1", 3128)
        )
        record(
            "app -> fw other port 8080 (listener exists)",
            "blocked",
            not can_connect("asta-app", "10.77.1.1", 8080),
        )
        record(
            f"app -> Internet {inet_ip}:443 (default route via fw, fw forwarding ON)",
            "blocked",
            not can_connect("asta-app", inet_ip, 443),
        )
        record(
            "app -> asta-cred 10.77.2.2:7443 (L3 link not attached)",
            "blocked",
            not can_connect("asta-app", "10.77.2.2", 7443),
        )
        record("app -> DNS udp/53 to the lab gateway", "blocked", not udp_probe("asta-app", LAB_GW))
        record(
            "fw -> app inbound 10.77.1.2:5432 (listener exists)",
            "blocked",
            not can_connect("asta-fw", "10.77.1.2", 5432),
        )
        # ---- asta-cred (vault/connector VM)
        record(
            "cred -> fw proxy 10.77.2.1:3128",
            "allowed",
            can_connect("asta-cred", "10.77.2.1", 3128),
        )
        record(
            "cred -> app 10.77.1.2:5432", "blocked", not can_connect("asta-cred", "10.77.1.2", 5432)
        )
        record(
            f"cred -> Internet {inet_ip}:443 (default route via fw)",
            "blocked",
            not can_connect("asta-cred", inet_ip, 443),
        )
        record(
            "fw -> cred inbound 10.77.2.2:7443 (listener exists)",
            "blocked",
            not can_connect("asta-fw", "10.77.2.2", 7443),
        )
        # ---- asta-fw (the only VM with an uplink)
        record(
            f"fw -> {inet_ip}:443 with an EMPTY allowlist",
            "blocked",
            not can_connect("asta-fw", inet_ip, 443),
        )
        nsx("asta-fw", f"nft add element inet asta_fw allowed_dst {{ {inet_ip} }}")
        ok = can_connect("asta-fw", inet_ip, 443)
        record(
            f"fw -> {inet_ip}:443 after allowlisting {inet_ip}",
            "allowed (needs real Internet)",
            ok,
            "" if ok else "no Internet from the lab VM or host blocked it",
        )
        record(
            f"fw -> {inet_ip}:80 (allowlisted IP, wrong port)",
            "blocked",
            not can_connect("asta-fw", inet_ip, 80),
        )
        record(
            "app -> allowlisted IP via fw is still not forwarded",
            "blocked",
            not can_connect("asta-app", inet_ip, 443),
        )
        record("fw DNS udp/53 to upstream as root", "allowed", udp_probe("asta-fw", LAB_GW))
        record(
            "fw DNS udp/53 to upstream as an unprivileged user (proxy user)",
            "blocked",
            not udp_probe("asta-fw", LAB_GW, user="nobody"),
        )
        # ---- defence in depth: a COMPROMISED app VM (its own firewall removed) must still not get through asta-fw
        nsx("asta-app", "nft flush ruleset")
        record(
            f"app with ITS firewall flushed -> Internet {inet_ip}:443 through asta-fw (forwarding ON)",
            "blocked by asta-fw",
            not can_connect("asta-app", inet_ip, 443),
        )
        record(
            "app with its firewall flushed -> fw port 8080 (listener exists)",
            "blocked by asta-fw",
            not can_connect("asta-app", "10.77.1.1", 8080),
        )
        record(
            "app with its firewall flushed -> asta-cred 10.77.2.2:7443 via fw",
            "blocked by asta-fw",
            not can_connect("asta-app", "10.77.2.2", 7443),
        )
        fwd = nsx("asta-fw", "nft list chain inet asta_fw forward", check=False).stdout
        m = re.search(r"counter packets (\d+)", fwd)
        record(
            "asta-fw forward chain dropped packets (counter > 0: the RULE blocked them)",
            "> 0",
            bool(m) and int(m.group(1)) > 0,
            fwd.strip().replace("\n", " ")[:110],
        )
        text = (Path(tempfile.gettempdir()) / "lab-app.nft").read_text()
        nsx(
            "asta-app", f"nft -f {Path(tempfile.gettempdir()) / 'lab-app.nft'}"
        )  # restore the app rules
        assert "asta_app" in text
        # ---- fail closed: take the firewall VM away
        sh("ip netns pids asta-fw | xargs -r kill", check=False)
        sh("ip netns del asta-fw", check=False)
        record(
            "gate stopped (asta-fw gone): app -> 10.77.1.1:3128",
            "blocked",
            not can_connect("asta-app", "10.77.1.1", 3128),
        )
        record(
            f"gate stopped: app -> Internet {inet_ip}:443",
            "blocked",
            not can_connect("asta-app", inet_ip, 443),
        )
    finally:
        teardown()
    failed = [r for r in R if r["result"] != "PASS"]
    out.write_text(
        json.dumps({"checks": R, "passed": len(R) - len(failed), "failed": len(failed)}, indent=1)
    )
    print(f"\n{len(R) - len(failed)}/{len(R)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
