"""M3A-MAC-01: Mac mini M4 (Apple Silicon) deployment design artifacts. Nothing is deployed or executed on a Mac."""

import importlib.util
import json
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MAC = ROOT / "infra" / "security-host" / "mac"
SCRIPTS = ROOT / "scripts"


def _git_bash() -> str | None:
    for c in (
        "C:/Program Files/Git/bin/bash.exe",
        "C:/Program Files/Git/usr/bin/bash.exe",
        "/bin/bash",
        "/usr/bin/bash",
    ):
        if Path(c).exists():
            return c
    return None


BASH = _git_bash()


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def unit(p: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for ln in read(p).splitlines():
        if "=" in ln and not ln.startswith(("#", "[")):
            k, v = ln.split("=", 1)
            out.setdefault(k.strip(), []).append(v.strip())
    return out


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


# ---------------------------------------------------------------- Phase A: the inventory is strictly read-only
MUTATING = re.compile(
    r"(\bsudo\b|\bbrew\s+(install|upgrade|tap|services)|\bdefaults\s+write|systemsetup\s+-set|pmset\s+-[acub]\b|"
    r"networksetup\s+-set|\brm\s|\bmv\s|diskutil\s+(erase|partition|apfs\s+(add|delete|resize))|fdesetup\s+(enable|disable)|"
    r"launchctl\s+(load|bootstrap|enable|disable|unload|bootout)|csrutil\s+(enable|disable)|softwareupdate|\bkill\b|>\s*/(?!dev/null))",
)


def test_mac_inventory_script_is_read_only():
    text = read(MAC / "mac-inventory.sh")
    code = "\n".join(ln for ln in text.splitlines() if not ln.strip().startswith("#"))
    assert not MUTATING.search(code), MUTATING.search(code)
    for needle in (
        "sw_vers",
        "hw.memsize",
        "fdesetup status",
        "df -H /",
        "networksetup -listallhardwareports",
        "tmutil destinationinfo",
        "pmset -g",
    ):
        assert needle in code, needle
    assert "installs nothing" in text and "no sudo" in text


@pytest.mark.skipif(BASH is None, reason="Git Bash not available")
@pytest.mark.parametrize(
    "script",
    ["mac-inventory.sh", "qemu-vms.sh", "app-vm/db_backup.sh", "app-vm/db_restore_verify.sh"],
)
def test_mac_shell_scripts_have_valid_syntax(script):
    assert BASH is not None
    r = subprocess.run([BASH, "-n", str(MAC / script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# ---------------------------------------------------------------- Phase B: VM topology and isolation
def test_qemu_script_is_dry_run_by_default_and_builds_only_isolated_unix_socket_links():
    t = read(MAC / "qemu-vms.sh")
    assert 'DRY_RUN="${DRY_RUN:-1}"' in t and "-accel hvf" in t and "qemu-system-aarch64" in t
    for forbidden in (
        "virtfs",
        "virtiofs",
        "9p",
        "-netdev user",
        "-nic ",
        "vmnet-host",
        "vmnet-shared",
        "-netdev tap",
        "usb",
        "-vnc",
        "-spice",
        "-monitor tcp",
    ):
        assert forbidden not in t, forbidden
    assert (
        t.count("addr.type=unix") >= 5 and "addr.type=inet" not in t
    )  # links and uplink are unix sockets only
    assert "l3.sock" not in t  # the app<->cred link is NOT attached until M3-B is authorized
    assert "chmod 700" in t and "-nodefaults" in t and "-display none" in t
    nics = t.split("nics()")[1].split('case "${1:-describe}"')[0]
    assert nics.count("virtio-net-pci") == 5  # fw: 3 (app0, cred0, up0), app: 1, cred: 1
    assert (
        nics.split("app)")[1].count("virtio-net-pci") == 2
    )  # app + cred branches end up with one each
    fw, rest = nics.split("app)")[0], nics.split("app)")[1]
    assert "up0" in fw and "up0" not in rest  # only the firewall VM has an uplink


def test_firewall_vm_never_forwards_and_accepts_only_the_two_private_links():
    t = read(MAC / "firewall-vm" / "nftables.conf")
    assert t.count("policy drop;") == 3
    assert (
        "chain forward" in t
        and "accept" not in t.split("chain forward")[1].split("chain output")[0]
    )
    inp = t.split("chain input")[1].split("chain forward")[0]
    accepts = [ln.strip() for ln in inp.splitlines() if ln.strip().endswith("accept")]
    assert accepts == [
        "iif lo accept",
        "ct state established,related accept",
        'iifname "app0"  ip saddr $APP_IP  tcp dport 3128 accept',
        'iifname "cred0" ip saddr $CRED_IP tcp dport 3128 accept',
    ]
    out = t.split("chain output")[1]
    assert 'oifname "up0" ip daddr @allowed_dst tcp dport { 443, 9443 } accept' in out
    for ln in out.splitlines():
        if "dport 53" in ln or "dport 123" in ln:
            assert "meta skuid 0" in ln


@pytest.mark.parametrize("vm,fw_ip", [("app-vm", "10.77.1.1"), ("cred-vm", "10.77.2.1")])
def test_app_and_credential_vms_reach_only_the_firewall_proxy(vm, fw_ip):
    t = read(MAC / vm / "nftables.conf")
    assert t.count("policy drop;") == 3 and f"define FW_IP = {fw_ip}" in t
    out = t.split("chain output")[1]
    active = [
        ln.strip()
        for ln in out.splitlines()
        if ln.strip().endswith("accept") and not ln.strip().startswith("#")
    ]
    assert active == [
        "oif lo accept",
        "ct state established,related accept",
        'oifname "fw0" ip daddr $FW_IP tcp dport 3128 accept',
    ]
    inp = t.split("chain input")[1].split("chain forward")[0]
    assert [
        ln.strip()
        for ln in inp.splitlines()
        if ln.strip().endswith("accept") and not ln.strip().startswith("#")
    ] == [
        "iif lo accept",
        "ct state established,related accept",
    ]
    net = read(MAC / vm / "netplan-50-asta.yaml")
    body = "\n".join(ln for ln in net.splitlines() if not ln.strip().startswith("#"))
    assert (
        "routes:" not in body
        and "nameservers:" not in body
        and "gateway" not in body
        and "dhcp4: false" in body
    )


def test_cred_vm_hosts_nothing_but_vault_and_connector_and_app_vm_never_holds_credentials():
    seeds = _seeds()
    cred = json.loads(seeds["cred"])
    enabled = " ".join(" ".join(c) for c in cred["runcmd"])
    assert (
        "asta-vault" not in enabled and "asta-connector" not in enabled
    )  # M3-B modules not enabled
    assert not any("postgres" in p for p in cred["packages"])  # no database on the credential VM
    app = json.loads(seeds["app"])
    paths = {w["path"] for w in app["write_files"]}
    assert not any(
        "vault" in p or "connector" in p for p in paths
    )  # no credential machinery on the app VM
    assert "postgresql-16" in app["packages"] and "age" in app["packages"]


def test_postgres_has_no_tcp_listener_and_peer_authentication_only():
    c = read(MAC / "app-vm" / "postgresql-hardening.conf")
    assert re.search(r"^listen_addresses = ''", c, re.M)
    hba = read(MAC / "app-vm" / "pg_hba.conf.asta")
    rules = [ln for ln in hba.splitlines() if ln and not ln.startswith("#")]
    assert any(r.startswith("local   asta  asta") and "peer" in r for r in rules)
    assert all(not r.startswith("host") or r.rstrip().endswith("reject") for r in rules)
    assert not re.search(
        r"\b(md5|password|trust|scram-sha-256)\b",
        "\n".join(r for r in rules if not r.startswith("#")),
    )


@pytest.mark.parametrize(
    "name",
    ["asta-paper-service.service", "asta-paper-supervisor.service"],
)
def test_app_services_are_hardened_and_can_only_talk_to_localhost_and_the_proxy(name):
    u = unit(MAC / "app-vm" / name)
    assert (
        u["NoNewPrivileges"] == ["yes"]
        and u["ProtectSystem"] == ["strict"]
        and u["User"] == ["asta-app"]
    )
    assert u["IPAddressDeny"] == ["any"] and set(u["IPAddressAllow"]) == {
        "10.77.1.1/32",
        "127.0.0.1/32",
    }
    assert (
        u["CapabilityBoundingSet"] == [""]
        and u["MemoryDenyWriteExecute"] == ["yes"]
        and u["LimitCORE"] == ["0"]
    )
    assert any("HTTPS_PROXY=http://10.77.1.1:3128" in v for v in u["Environment"])


def test_supervisor_unit_uses_the_posix_entry_point_and_the_backup_job_has_no_network():
    s = unit(MAC / "app-vm" / "asta-paper-supervisor.service")
    assert "paper_supervisor_posix.py" in s["ExecStart"][0] and s["Restart"] == ["always"]
    b = unit(MAC / "app-vm" / "asta-db-backup.service")
    assert b["PrivateNetwork"] == ["yes"] and b["RestrictAddressFamilies"] == ["AF_UNIX"]
    sh = read(MAC / "app-vm" / "db_backup.sh")
    assert "age -R" in sh and sh.index("age -R") < sh.index("mv ") and "backup-recipient.pub" in sh
    assert "-----BEGIN" not in sh and "AGE-SECRET-KEY" not in sh  # no private key anywhere


def test_proxy_unit_for_the_mac_listens_on_both_links_without_dns_or_capabilities():
    u = unit(MAC / "firewall-vm" / "asta-egress-proxy.service")
    assert "EGRESS_LISTEN_HOST=0.0.0.0" in " ".join(u["Environment"]) and u["DynamicUser"] == [
        "yes"
    ]
    assert set(u["IPAddressAllow"]) == {"10.77.1.0/30", "10.77.2.0/30", "0.0.0.0/0"}
    assert "/etc/asta-egress/hosts:/etc/hosts" in u["BindReadOnlyPaths"][0]


# ---------------------------------------------------------------- host boot recovery (launchd)
@pytest.mark.parametrize("vm", ["fw", "app", "cred"])
def test_launchd_vm_daemons_start_at_boot_run_unprivileged_and_restart(vm):
    d = plistlib.loads((MAC / "launchd" / f"com.asta.vm.{vm}.plist").read_bytes())
    assert d["Label"] == f"com.asta.vm.{vm}" and d["UserName"] == "_astavm"
    assert d["RunAtLoad"] is True and d["KeepAlive"] is True and d["ThrottleInterval"] >= 5
    assert d["ProgramArguments"][-2:] == ["run", vm]
    assert d["EnvironmentVariables"]["DRY_RUN"] == "0"


def test_only_the_socket_vmnet_helper_runs_as_root_and_exposes_a_group_restricted_socket():
    d = plistlib.loads((MAC / "launchd" / "com.asta.socket_vmnet.plist").read_bytes())
    assert d["UserName"] == "root" and "--socket-group=_astavm" in d["ProgramArguments"]
    assert "--vmnet-mode=bridged" in d["ProgramArguments"]
    others = [plistlib.loads(p.read_bytes()) for p in (MAC / "launchd").glob("com.asta.vm.*.plist")]
    assert all(o["UserName"] != "root" for o in others)


# ---------------------------------------------------------------- reproducible seeds and nft -c
def _seeds() -> dict[str, str]:
    b = load(ROOT / "infra" / "security-host" / "provision" / "build_seed.py", "build_seed_mac")
    import tempfile

    out: dict[str, str] = {}
    with tempfile.TemporaryDirectory() as t:
        s1 = b.emit_mac(Path(t) / "a")
        s2 = b.emit_mac(Path(t) / "b")
        assert s1 == s2  # reproducible
        for vm in ("fw", "app", "cred"):
            text = (Path(t) / "a" / vm / "user-data").read_text(encoding="utf-8")
            assert (
                text.startswith("#cloud-config\n")
                and "ssh-rsa" not in text
                and "BEGIN " not in text
            )
            out[vm] = text.split("\n", 1)[1]
    return out


def test_mac_seeds_are_reproducible_carry_the_reviewed_files_and_embed_no_secret():
    seeds = _seeds()
    fw = json.loads(seeds["fw"])
    assert ["nft", "-c", "-f", "/etc/nftables.conf"] in fw["runcmd"]
    files = {w["path"]: w["content"] for w in fw["write_files"]}
    assert files["/etc/nftables.conf"] == read(MAC / "firewall-vm" / "nftables.conf").replace(
        "\r\n", "\n"
    )
    for vm in ("fw", "app", "cred"):
        cfg = json.loads(seeds[vm])
        assert cfg["ssh_pwauth"] is False and cfg["users"][0]["ssh_authorized_keys"] == [
            "__OPERATOR_SSH_PUBLIC_KEY__"
        ]
        assert not re.search(
            r"(SECRET_KEY|PASSWORD|AGE-SECRET|api[_-]?key)\s*[=:]\s*\S{8,}", seeds[vm], re.I
        )


def _docker_ok() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=30).returncode == 0
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _docker_ok(), reason="Docker not available")
@pytest.mark.parametrize("vm", ["firewall-vm", "app-vm", "cred-vm"])
def test_mac_nftables_files_pass_nft_check_only(vm):
    r = subprocess.run(
        ["docker", "run", "--rm", "--cap-drop", "ALL", "--cap-add", "NET_ADMIN", "-v", f"{MAC / vm}:/cfg:ro", "alpine:3.20", "sh", "-c",
         "apk add -q --no-cache nftables >/dev/null 2>&1 && nft -c -f /cfg/nftables.conf"],
        capture_output=True, text=True, timeout=300, env={**__import__("os").environ, "MSYS_NO_PATHCONV": "1"},
    )  # fmt: skip
    assert r.returncode == 0, r.stderr


# ---------------------------------------------------------------- Linux supervisor entry point (Windows one untouched)
def test_posix_supervisor_finds_and_stops_only_marked_processes_in_a_fake_proc_tree(tmp_path):
    m = load(SCRIPTS / "paper_supervisor_posix.py", "paper_supervisor_posix")
    proc = tmp_path / "proc"
    for pid, cmd, state in (
        (101, b"python\0-m\0asta.paper_service\0", "S"),
        (102, b"python\0other.py\0", "S"),
        (103, b"python\0-m\0asta.paper_service\0", "Z"),
    ):
        d = proc / str(pid)
        d.mkdir(parents=True)
        (d / "cmdline").write_bytes(cmd)
        (d / "stat").write_text(f"{pid} (python) {state} 1 1 1")
    (proc / "self").mkdir()
    assert m.find_by_cmdline("asta.paper_service", proc) == [101, 103]
    assert (
        m.pid_running(101, proc) is True and m.pid_running(103, proc) is False
    )  # zombie is not running
    assert m.pid_running(999, proc) is False
    killed: list[tuple[int, int]] = []

    def fake_kill(pid: int, sig: int) -> None:
        killed.append((pid, sig))
        if sig != 0:
            shutil.rmtree(
                proc / str(pid), ignore_errors=True
            )  # process disappears after the signal

    got = m.kill_orphans("asta.paper_service", proc, fake_kill, grace_s=0.5)
    assert (
        got == [101, 103] and [p for p, _ in killed] == [101, 103] and not (proc / "101").exists()
    )
    assert (proc / "102").exists()  # the unrelated process was not touched
    src = read(SCRIPTS / "paper_supervisor_posix.py")
    assert not re.search(
        r"os.kill(s*pids*,s*0s*)", src
    )  # would terminate a process on Windows; never used
    base_src = read(SCRIPTS / "paper_supervisor.py")
    assert (
        "paper_supervisor_posix" not in base_src
    )  # the Windows supervisor was not modified for Linux


def test_posix_entry_point_refuses_to_run_on_windows():
    m = load(SCRIPTS / "paper_supervisor_posix.py", "paper_supervisor_posix2")
    if sys.platform == "win32":
        assert m.main() == 2


# ---------------------------------------------------------------- ARM64 compatibility evidence
def test_arm64_dependency_audit_matches_the_lockfile():
    a = load(SCRIPTS / "arm64_compat_audit.py", "arm64_compat_audit")
    assert a.classify(["x-1-py3-none-any.whl"]) == (True, True, "pure-python")
    assert (
        a.classify(["x-1-cp312-cp312-manylinux2014_aarch64.manylinux_2_17_aarch64.whl"])[0] is True
    )
    assert a.classify(["x-1-cp312-cp312-win_amd64.whl"]) == (False, False, "sdist-only/none")
    ev = json.loads(read(ROOT / "docs" / "evidence" / "m3a" / "mac-arm64-dependency-audit.json"))
    assert (
        ev["needs_build_on_linux_aarch64"] == ["pgserver"]
        and ev["needs_build_on_macos_arm64"] == []
    )
    wheels = read(ROOT / "docs" / "evidence" / "m3a" / "mac-arm64-runtime-wheels.sha256")
    assert len([ln for ln in wheels.splitlines() if ln.strip() and not ln.startswith("#")]) >= 30
    assert "cryptography" in wheels and "pydantic_core" in wheels and "aarch64" in wheels


# ---------------------------------------------------------------- M3-B-R3: bridge supervision units
def test_bridge_unit_is_hardened_database_socket_only_and_always_restarted():
    u = unit(MAC / "app-vm" / "asta-risk-bridge.service")
    assert u["User"] == ["asta-bridge"] and u["Restart"] == ["always"]
    assert u["PrivateNetwork"] == ["yes"] and u["RestrictAddressFamilies"] == ["AF_UNIX"]
    assert u["NoNewPrivileges"] == ["yes"] and u["CapabilityBoundingSet"] == [""]
    assert u["ExecStart"] == ["/opt/asta/venv/bin/python -m asta.orders.risk_bridge"]
    assert u["StartLimitIntervalSec"] == ["0"]  # never gives up restarting


def test_monitor_runs_every_30_seconds_as_its_own_user_and_a_critical_exit_raises_the_alert():
    s = unit(MAC / "app-vm" / "asta-security-monitor.service")
    assert s["User"] == ["asta-monitor"] and s["SuccessExitStatus"] == [
        "0 1"
    ]  # 2 (critical) fails the unit
    assert s["OnFailure"] == ["asta-security-alert@%n.service"]
    t = read(MAC / "app-vm" / "asta-security-monitor.timer")
    assert "OnUnitActiveSec=30" in t
    assert (MAC / "app-vm" / "asta-security-alert@.service").exists()


def test_the_service_roles_are_distinct_and_the_release_role_runs_no_service():
    units = [
        p for p in (MAC / "app-vm").glob("*.service") if p.name != "asta-security-alert@.service"
    ]
    users = {p.name: unit(p).get("User", [""])[0] for p in units if "User" in unit(p)}
    assert users["asta-risk-bridge.service"] != users["asta-paper-service.service"]
    assert users["asta-security-monitor.service"] not in {
        users["asta-risk-bridge.service"],
        users["asta-paper-service.service"],
    }
    sql = read(MAC / "app-vm" / "pg_roles_m3b.sql")
    assert "release_kill_switch" in sql and "TO asta_release" in sql
    assert not any("asta_release" in read(p) for p in units)  # no daemon runs as the release role


def test_every_service_role_has_peer_only_authentication_and_a_distinct_os_user():
    hba = read(MAC / "app-vm" / "pg_hba.conf.asta")
    ident = read(MAC / "app-vm" / "pg_ident.conf.asta")
    roles = (
        ("asta_bridge", "asta-bridge"),
        ("asta_orders", "asta-orders"),
        ("asta_monitor", "asta-monitor"),
        ("asta_release", "asta-release"),
    )
    for role, osuser in roles:
        assert re.search(rf"local\s+asta\s+{role}\s+peer map=", hba), role
        assert re.search(rf"{osuser}\s+{role}\b", ident), role
    active = "\n".join(ln for ln in hba.splitlines() if not ln.startswith("#"))
    assert not re.search(r"\b(md5|password|trust|scram-sha-256)\b", active)


# ---------------------------------------------------------------- M3-B-R4: two-VM phase, host-network bypass
def vm_cmd(vm: str, **env: str) -> str:
    import os
    import subprocess

    r = subprocess.run(  # noqa: S603
        ["/bin/sh", str(MAC / "qemu-vms.sh"), "run", vm],
        capture_output=True, text=True, timeout=30,
        env={"DRY_RUN": "1", "PATH": "/usr/bin:/bin", **env, "HOME": os.environ.get("HOME", "/")},
    )  # fmt: skip
    return r.stdout + r.stderr


def test_two_vm_phase_the_credential_vm_cannot_start_and_the_firewall_has_no_cred_link():
    fw = vm_cmd("fw")
    assert (
        "cred0" not in fw and "l2.sock" not in fw and fw.count("-netdev") == 2
    )  # app link + uplink only
    out = vm_cmd("cred")
    assert "REFUSING" in out and "qemu-system-aarch64 -name" not in out
    assert "cred0" in vm_cmd(
        "fw", ASTA_ENABLE_CRED="1"
    )  # the later phase attaches it, deliberately


def test_no_vm_has_a_host_network_file_share_usb_or_forwarding_device():
    for vm, env in (
        ("fw", {}),
        ("app", {}),
        ("fw", {"ASTA_ENABLE_CRED": "1"}),
        ("cred", {"ASTA_ENABLE_CRED": "1"}),
    ):
        cmd = vm_cmd(vm, **env)
        line = next(ln for ln in cmd.splitlines() if ln.startswith("> qemu-system-aarch64"))
        for banned in (
            "hostfwd",
            "-nic",
            "vmnet-",
            "tap,",
            "user,",
            "-virtfs",
            "-usb",
            "usb-",
            "-fsdev",
            "vhost-user",
            "-spice",
            "-vnc",
        ):
            assert banned not in line, (vm, banned)
        assert all("type=unix" in n for n in line.split("-netdev ")[1:]), (
            vm
        )  # every NIC is a unix-socket stream
        assert "-display none" in line and "-monitor none" in line and "-nodefaults" in line
    app = next(ln for ln in vm_cmd("app").splitlines() if ln.startswith("> qemu-system-aarch64"))
    assert (
        app.count("-netdev") == 1 and "socket_vmnet" not in app
    )  # the app VM has exactly one NIC, to the firewall VM


def test_two_vm_phase_sizes_fit_the_contended_host():
    sizes = {"fw": (1, 768, 8), "app": (3, 3072, 40), "cred": (1, 1024, 10)}
    text = read(MAC / "qemu-vms.sh")
    for vm, (cpu, ram, disk) in sizes.items():
        assert f'echo "{cpu} {ram} {disk}"' in text, vm
    assert (
        sizes["fw"][1] + sizes["app"][1] <= 4096
    )  # the two-VM phase stays within 4 GiB of guest RAM


def test_security_services_installer_is_dry_run_by_default_linux_only_and_password_free():
    import subprocess

    script = MAC / "app-vm" / "install-security-services.sh"
    text = read(script)
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert "APPLY=0" in code and "needs root" in code and "not the Mac host" in code
    assert "password" not in "\n".join(
        ln for ln in code.splitlines() if "ASTA_DATABASE_URL" in ln
    ).lower().replace("no password", "")
    assert "--no-create-home" in code and "/usr/sbin/nologin" in code
    for u in (
        "asta-risk-bridge.service",
        "asta-security-monitor.timer",
        "asta-security-alert@.service",
        "pg_roles_m3b.sql",
    ):
        assert u in code
    assert (
        "asta-orders" in code and "only when the order path is authorised" in code
    )  # the order worker is NOT installed
    r = subprocess.run(["/bin/sh", str(script)], capture_output=True, text=True, timeout=30)  # noqa: S603
    assert r.returncode == 1 and "Linux app VM" in r.stderr  # on macOS it refuses at once


# ---------------------------------------------------------------- M3-B-R4: host separation check
def test_host_separation_check_is_read_only_and_reports_the_current_shared_state_as_failing(
    tmp_path,
):
    import json
    import re
    import subprocess

    script = MAC / "host-separation-check.sh"
    code = "\n".join(ln for ln in read(script).splitlines() if not ln.lstrip().startswith("#"))
    for banned in (
        "docker stop",
        "docker rm",
        "docker kill",
        "docker rmi",
        "docker volume rm",
        "docker system prune",
        "kill ",
        "pkill",
        "launchctl bootout",
        "launchctl unload",
        "sudo",
        "rm ",
        "diskutil",
        "networksetup",
        "systemsetup",
    ):
        assert banned not in code, banned
    assert not re.search(
        r"\b(stop|rm|prune|delete)\b", " ".join(re.findall(r"docker [a-z]+", code))
    ), "only read verbs of docker are used"
    out = tmp_path / "r.json"
    r = subprocess.run(
        ["/bin/sh", str(script), str(out)], capture_output=True, text=True, timeout=120
    )  # noqa: S603
    d = json.loads(out.read_text())
    assert {c["check"] for c in d["host_separation"]} >= {
        "no_lan_reachable_listeners",
        "filevault_on",
        "app_firewall_on",
        "no_exo",
    }
    assert r.returncode == (0 if d["failed"] == 0 else 1)


def test_the_prepared_sshd_dropin_is_key_only_restricted_and_has_no_forwarding():
    conf = "\n".join(
        ln for ln in read(MAC / "sshd-asta.conf").splitlines() if ln and not ln.startswith("#")
    )
    for want in (
        "PasswordAuthentication no",
        "KbdInteractiveAuthentication no",
        "PermitRootLogin no",
        "AuthenticationMethods publickey",
        "AllowUsers apple",
        "AllowTcpForwarding no",
        "X11Forwarding no",
    ):
        assert want in conf, want
    assert "PasswordAuthentication yes" not in conf


# ---------------------------------------------------------------- M3-B-R5: dead-man's-switch (firewall VM)
def test_deadman_is_disabled_until_configured_https_only_and_never_prints_the_url(tmp_path):
    import subprocess

    script = MAC / "firewall-vm" / "asta-deadman.sh"
    text = read(script)
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert (
        "http://" not in code and "hc-ping" not in code and "https://" in code
    )  # no destination in git; https enforced
    assert (
        "--fail" in code
        and "not printing the URL" in code
        and 'echo "$URL"' not in code
        and 'echo "$DEADMAN_URL"' not in code
    )
    assert "ruleset.sha256" in code and "ip_forward" in code and "asta-egress-proxy.service" in code
    r = subprocess.run(["/bin/sh", str(script)], capture_output=True, text=True, timeout=30)  # noqa: S603
    assert r.returncode == 0 and "not configured" in r.stdout  # nothing sent without configuration
    assert "OnUnitActiveSec=60" in read(MAC / "firewall-vm" / "asta-deadman.timer")
    assert "asta-deadman.sh" in read(MAC / "firewall-vm" / "asta-deadman.service")
