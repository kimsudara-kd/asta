"""The optional pf anchor renderer: public addresses only, confines only the credential user, loads nothing."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "render_pf_anchor.py"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)  # noqa: S603


def test_renders_a_user_scoped_rule_set():
    r = run("--testnet-ips", "13.35.1.1,13.35.1.2", "--dns", "8.8.8.8,1.1.1.1")
    assert r.returncode == 0
    out = r.stdout
    assert "table <asta_testnet> persist { 13.35.1.1 13.35.1.2 }" in out
    assert (
        "pass out quick proto tcp from any to <asta_testnet> port 443 user asta-cred keep state"
        in out
    )
    assert out.strip().splitlines()[-1] == "block drop out quick user asta-cred"
    assert (
        "user !=" not in out and "block drop out quick proto" not in out
    )  # no rule can affect other users
    assert all(
        ("asta-cred" in ln) or ln.startswith(("#", "table")) or not ln for ln in out.splitlines()
    )


@pytest.mark.parametrize(
    "bad",
    [
        "10.0.0.1",
        "192.168.1.1",
        "172.16.0.1",
        "127.0.0.1",
        "169.254.1.1",
        "100.64.1.1",
        "224.0.0.1",
        "0.0.0.0",
        "255.255.255.255",
        "::1",
        "example.com",
        "",
        "8.8.8.8,8.8.8.8",
    ],
)
def test_private_special_malformed_or_duplicate_addresses_are_refused(bad):
    for args in (
        ("--testnet-ips", bad, "--dns", "8.8.8.8"),
        ("--testnet-ips", "13.35.1.1", "--dns", bad),
    ):
        r = run(*args)
        assert r.returncode == 1 and "REFUSING" in r.stderr and r.stdout == ""


def test_it_only_prints_and_takes_exactly_the_two_options():
    assert run().returncode == 1
    assert run("--testnet-ips", "13.35.1.1").returncode == 1
    text = SCRIPT.read_text()
    for bad in ("subprocess", "os.system", "pfctl(", "open("):
        assert bad not in text
