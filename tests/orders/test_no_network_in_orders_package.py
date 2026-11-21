"""Review R-12: the order-path package may not open network connections, except the alert notifier.

Complements (does not modify) tests/live_lock. alerting.py is the ONLY module allowed a network
client, limited to urllib.request/urllib.parse; its Notifier refuses all but https or loopback http.
"""

import ast
from pathlib import Path

ORDERS = Path(__file__).resolve().parents[2] / "src" / "asta" / "orders"
BANNED = {
    "socket",
    "ssl",
    "http",
    "httpx",
    "requests",
    "websockets",
    "websocket",
    "aiohttp",
    "ftplib",
    "smtplib",
}
ALLOWED_EXCEPTION = {"alerting.py": {"urllib"}}


def _imports(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


def test_only_the_notifier_may_use_urllib_and_nothing_in_orders_uses_a_socket_library():
    offenders = []
    for p in sorted(ORDERS.glob("*.py")):
        for mod in _imports(p):
            top = mod.split(".")[0]
            if top in BANNED or (
                top == "urllib" and top not in ALLOWED_EXCEPTION.get(p.name, set())
            ):
                offenders.append(f"{p.name}: {mod}")
    assert not offenders, offenders


def test_the_notifier_uses_only_urllib_request_and_parse():
    mods = {m for m in _imports(ORDERS / "alerting.py") if m.startswith("urllib")}
    assert mods <= {"urllib.request", "urllib.parse"}, mods
