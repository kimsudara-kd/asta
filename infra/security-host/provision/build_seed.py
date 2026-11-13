"""Builds reproducible cloud-init seeds for the two Ubuntu 24.04 VMs (M3A-R3). Nothing is executed.

    python infra/security-host/provision/build_seed.py <outdir>

Writes <outdir>/{firewall-vm,runtime-vm}/{user-data,meta-data,network-config} from the version-
controlled configuration in infra/security-host and infra/egress, plus SHA256SUMS of the inputs so a
reviewer can reproduce the seed byte for byte. user-data is JSON, which is valid YAML ("#cloud-config"
header + JSON body); file contents are embedded verbatim. No secret, no key and no credential is
embedded: the operator's SSH PUBLIC key replaces the placeholder at provisioning time, the vault
unlock secret is entered by the operator and is never written to a seed.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SH = ROOT / "infra" / "security-host"
EG = ROOT / "infra" / "egress"
PLACEHOLDER_KEY = "__OPERATOR_SSH_PUBLIC_KEY__"


def f(src: Path, dest: str, mode: str = "0644", owner: str = "root:root") -> dict[str, str]:
    return {
        "path": dest,
        "permissions": mode,
        "owner": owner,
        "content": src.read_text(encoding="utf-8").replace("\r\n", "\n"),
    }


def common(hostname: str) -> dict[str, object]:
    return {
        "hostname": hostname,
        "manage_etc_hosts": True,
        "package_update": False,  # no Internet during first boot; packages come from the install media
        "ssh_pwauth": False,
        "disable_root": True,
        "users": [
            {
                "name": "asta-admin",
                "groups": ["sudo"],
                "shell": "/bin/bash",
                "lock_passwd": True,
                "sudo": "ALL=(ALL) ALL",
                "ssh_authorized_keys": [PLACEHOLDER_KEY],
            }
        ],
        "write_files": [
            {
                "path": "/etc/sysctl.d/60-asta-hardening.conf",
                "permissions": "0644",
                "content": "net.ipv6.conf.all.disable_ipv6=1\nnet.ipv6.conf.default.disable_ipv6=1\n"
                "net.ipv4.ip_forward=0\nnet.ipv4.conf.all.accept_redirects=0\n"
                "net.ipv4.conf.all.send_redirects=0\nnet.ipv4.conf.all.rp_filter=1\n"
                "kernel.core_pattern=|/bin/false\nfs.suid_dumpable=0\n",
            }
        ],
    }


def firewall() -> dict[str, object]:
    c = common("asta-fw")
    c["write_files"] += [  # type: ignore[operator]
        f(SH / "firewall-vm" / "nftables.conf", "/etc/nftables.conf"),
        f(
            SH / "firewall-vm" / "asta-allowlist-refresh.sh",
            "/usr/local/sbin/asta-allowlist-refresh.sh",
            "0750",
        ),
        f(
            SH / "firewall-vm" / "asta-allowlist-refresh.service",
            "/etc/systemd/system/asta-allowlist-refresh.service",
        ),
        f(
            SH / "firewall-vm" / "asta-allowlist-refresh.timer",
            "/etc/systemd/system/asta-allowlist-refresh.timer",
        ),
        f(
            SH / "firewall-vm" / "asta-egress-proxy.service",
            "/etc/systemd/system/asta-egress-proxy.service",
        ),
        f(SH / "firewall-vm" / "netplan-50-asta.yaml", "/etc/netplan/50-asta.yaml", "0600"),
        f(EG / "proxy.py", "/opt/asta-egress/proxy.py", "0644"),
        f(EG / "allowlist.json", "/etc/asta-egress/allowlist.json"),
        {
            "path": "/etc/asta-egress/empty-resolv.conf",
            "permissions": "0644",
            "content": "# intentionally empty\n",
        },
        {
            "path": "/etc/asta-egress/hosts",
            "permissions": "0644",
            "content": "127.0.0.1 localhost\n",
        },
    ]
    c["runcmd"] = [
        ["sysctl", "--system"],
        ["nft", "-c", "-f", "/etc/nftables.conf"],
        ["systemctl", "enable", "--now", "nftables.service"],
        ["systemctl", "daemon-reload"],
        [
            "systemctl",
            "enable",
            "--now",
            "asta-allowlist-refresh.timer",
            "asta-egress-proxy.service",
        ],
        ["netplan", "apply"],
    ]
    c["packages"] = ["nftables", "jq", "python3", "chrony", "systemd-timesyncd-"]
    return c


def runtime() -> dict[str, object]:
    c = common("asta-rt")
    c["write_files"] += [  # type: ignore[operator]
        f(SH / "runtime-vm" / "nftables.conf", "/etc/nftables.conf"),
        f(SH / "runtime-vm" / "netplan-50-asta.yaml", "/etc/netplan/50-asta.yaml", "0600"),
        f(SH / "runtime-vm" / "asta-vault.service", "/etc/systemd/system/asta-vault.service"),
        f(SH / "runtime-vm" / "asta-vault.socket", "/etc/systemd/system/asta-vault.socket"),
        f(
            SH / "runtime-vm" / "asta-connector.service",
            "/etc/systemd/system/asta-connector.service",
        ),
    ]
    c["groups"] = ["asta-connector", "asta-vault", "asta-app"]
    c["users"] += [  # type: ignore[operator]
        {
            "name": "asta-vault",
            "system": True,
            "shell": "/usr/sbin/nologin",
            "lock_passwd": True,
            "primary_group": "asta-vault",
        },
        {
            "name": "asta-connector",
            "system": True,
            "shell": "/usr/sbin/nologin",
            "lock_passwd": True,
            "primary_group": "asta-connector",
        },
        {
            "name": "asta-app",
            "system": True,
            "shell": "/usr/sbin/nologin",
            "lock_passwd": True,
            "primary_group": "asta-app",
        },
    ]
    # NOTE: the vault and connector units are NOT enabled: their modules do not exist until M3-B.
    c["runcmd"] = [
        ["sysctl", "--system"],
        ["nft", "-c", "-f", "/etc/nftables.conf"],
        ["systemctl", "enable", "--now", "nftables.service"],
        ["netplan", "apply"],
    ]
    c["packages"] = ["nftables", "python3", "python3-venv", "chrony"]
    return c


def network(role: str) -> str:
    # cloud-init network-config v2 mirrors the netplan file; kept minimal and static
    return "version: 2\nethernets: {}\n"  # the netplan file written above is authoritative


def emit(outdir: Path) -> dict[str, str]:
    sums: dict[str, str] = {}
    for name, cfg in (("firewall-vm", firewall()), ("runtime-vm", runtime())):
        d = outdir / name
        d.mkdir(parents=True, exist_ok=True)
        user = "#cloud-config\n" + json.dumps(cfg, indent=1, sort_keys=True) + "\n"
        (d / "user-data").write_text(user, encoding="utf-8", newline="\n")
        (d / "meta-data").write_text(
            f"instance-id: asta-{name}-1\nlocal-hostname: asta-{'fw' if name.startswith('fire') else 'rt'}\n",
            encoding="utf-8",
            newline="\n",
        )  # noqa: E501
        (d / "network-config").write_text(network(name), encoding="utf-8", newline="\n")
        for fn in ("user-data", "meta-data", "network-config"):
            sums[f"{name}/{fn}"] = hashlib.sha256((d / fn).read_bytes()).hexdigest()
    inputs = sorted(list(SH.rglob("*")) + [EG / "proxy.py", EG / "allowlist.json"])
    lines = [
        f"{hashlib.sha256(p.read_bytes().replace(b'\r\n', b'\n')).hexdigest()}  {p.relative_to(ROOT).as_posix()}"
        for p in inputs
        if p.is_file() and "provision/out" not in p.as_posix()
    ]
    (outdir / "INPUT-SHA256SUMS").write_text(
        "\n".join(lines) + "\n", encoding="utf-8", newline="\n"
    )
    return sums


def main() -> int:
    outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "seed-out"
    print(json.dumps(emit(outdir), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
