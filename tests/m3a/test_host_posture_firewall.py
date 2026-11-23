"""The firewall reading counts every ON state (1 and 2) and fails closed on anything unparseable."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from host_posture import firewall_on  # noqa: E402


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Firewall is enabled. (State = 1)", True),
        ("Firewall is blocking all non-essential incoming connections. (State = 2)", True),
        ("Firewall is disabled. (State = 0)", False),
        ("", False),
        ("garbage", False),
        ("Firewall is disabled.", False),
    ],
)
def test_firewall_state_parsing(text: str, expected: bool) -> None:
    assert firewall_on(text) is expected
