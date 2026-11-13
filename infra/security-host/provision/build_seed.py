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


MAC = SH / "mac"
NL = "\n"


def _tail(name: str) -> list[dict[str, str]]:
    return [
        {
            "path": "/etc/asta-egress/empty-resolv.conf",
            "permissions": "0644",
            "content": "# intentionally empty" + NL,
        },
        {
            "path": "/etc/asta-egress/hosts",
            "permissions": "0644",
            "content": "127.0.0.1 localhost" + NL,
        },
    ]


def _runcmd(extra: list[list[str]] | None = None) -> list[list[str]]:
    base = [
        ["sysctl", "--system"],
        ["nft", "-c", "-f", "/etc/nftables.conf"],
        ["systemctl", "enable", "--now", "nftables.service"],
    ]
    return base + (extra or []) + [["netplan", "apply"]]


def mac_fw() -> dict[str, object]:
    c = common("asta-fw")
    fw = MAC / "firewall-vm"
    c["write_files"] += [  # type: ignore[operator]
        f(fw / "nftables.conf", "/etc/nftables.conf"),
        f(fw / "netplan-50-asta.yaml", "/etc/netplan/50-asta.yaml", "0600"),
        f(fw / "asta-allowlist-refresh.sh", "/usr/local/sbin/asta-allowlist-refresh.sh", "0750"),
        f(
            fw / "asta-allowlist-refresh.service",
            "/etc/systemd/system/asta-allowlist-refresh.service",
        ),
        f(fw / "asta-allowlist-refresh.timer", "/etc/systemd/system/asta-allowlist-refresh.timer"),
        f(fw / "asta-egress-proxy.service", "/etc/systemd/system/asta-egress-proxy.service"),
        f(EG / "proxy.py", "/opt/asta-egress/proxy.py"),
        f(EG / "allowlist.json", "/etc/asta-egress/allowlist.json"),
        *_tail("fw"),
    ]
    c["runcmd"] = _runcmd(
        [
            ["systemctl", "daemon-reload"],
            [
                "systemctl",
                "enable",
                "--now",
                "asta-allowlist-refresh.timer",
                "asta-egress-proxy.service",
            ],
        ]
    )
    c["packages"] = ["nftables", "jq", "python3", "chrony"]
    return c


def mac_app() -> dict[str, object]:
    c = common("asta-app")
    app = MAC / "app-vm"
    c["groups"] = ["asta-app"]
    c["users"] += [  # type: ignore[operator]
        {
            "name": "asta-app",
            "system": True,
            "shell": "/usr/sbin/nologin",
            "lock_passwd": True,
            "primary_group": "asta-app",
            "homedir": "/var/lib/asta",
        }
    ]
    sec = "/opt/asta/src/infra/security-host/mac/app-vm/"
    c["write_files"] += [  # type: ignore[operator]
        f(app / "nftables.conf", "/etc/nftables.conf"),
        f(app / "netplan-50-asta.yaml", "/etc/netplan/50-asta.yaml", "0600"),
        f(app / "asta-paper-service.service", "/etc/systemd/system/asta-paper-service.service"),
        f(
            app / "asta-paper-supervisor.service",
            "/etc/systemd/system/asta-paper-supervisor.service",
        ),
        f(app / "asta-db-backup.service", "/etc/systemd/system/asta-db-backup.service"),
        f(app / "asta-db-backup.timer", "/etc/systemd/system/asta-db-backup.timer"),
        f(
            app / "postgresql-hardening.conf",
            "/etc/postgresql/16/main/conf.d/asta-hardening.conf",
            "0644",
            "postgres:postgres",
        ),
        f(
            app / "pg_hba.conf.asta",
            "/etc/postgresql/16/main/pg_hba.conf.asta",
            "0640",
            "postgres:postgres",
        ),
        f(
            app / "pg_ident.conf.asta",
            "/etc/postgresql/16/main/pg_ident.conf.asta",
            "0640",
            "postgres:postgres",
        ),
        f(app / "db_backup.sh", sec + "db_backup.sh", "0755"),
        f(app / "db_restore_verify.sh", sec + "db_restore_verify.sh", "0755"),
    ]
    # /etc/asta/asta.env (application secret key) is generated ON the VM at provisioning, never carried in a seed;
    # the ASTA runtime (wheels + source) arrives on a separate read-only payload disk.
    c["runcmd"] = _runcmd()
    c["packages"] = ["nftables", "postgresql-16", "python3.12", "python3.12-venv", "age", "chrony"]
    return c


def mac_cred() -> dict[str, object]:
    c = common("asta-cred")
    cr = MAC / "cred-vm"
    c["groups"] = ["asta-connector", "asta-vault"]
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
    ]
    c["write_files"] += [  # type: ignore[operator]
        f(cr / "nftables.conf", "/etc/nftables.conf"),
        f(cr / "netplan-50-asta.yaml", "/etc/netplan/50-asta.yaml", "0600"),
        f(SH / "runtime-vm" / "asta-vault.service", "/etc/systemd/system/asta-vault.service"),
        f(SH / "runtime-vm" / "asta-vault.socket", "/etc/systemd/system/asta-vault.socket"),
        f(
            SH / "runtime-vm" / "asta-connector.service",
            "/etc/systemd/system/asta-connector.service",
        ),
    ]
    c["runcmd"] = _runcmd()  # vault/connector units are NOT enabled: the M3-B modules do not exist
    c["packages"] = ["nftables", "python3.12", "python3.12-venv", "chrony"]
    return c


def emit_mac(outdir: Path) -> dict[str, str]:
    sums: dict[str, str] = {}
    for name, cfg, host in (
        ("fw", mac_fw(), "asta-fw"),
        ("app", mac_app(), "asta-app"),
        ("cred", mac_cred(), "asta-cred"),
    ):
        d = outdir / name
        d.mkdir(parents=True, exist_ok=True)
        user = "#cloud-config" + NL + json.dumps(cfg, indent=1, sort_keys=True) + NL
        (d / "user-data").write_text(user, encoding="utf-8", newline=NL)
        (d / "meta-data").write_text(
            f"instance-id: asta-mac-{name}-1{NL}local-hostname: {host}{NL}",
            encoding="utf-8",
            newline=NL,
        )
        (d / "network-config").write_text(
            f"version: 2{NL}ethernets: {{}}{NL}", encoding="utf-8", newline=NL
        )
        for fn in ("user-data", "meta-data", "network-config"):
            sums[f"{name}/{fn}"] = hashlib.sha256((d / fn).read_bytes()).hexdigest()
    return sums


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    outdir = Path(args[0]) if args else ROOT / "data" / "seed-out"
    print(json.dumps(emit_mac(outdir) if "--target=mac" in sys.argv else emit(outdir), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
