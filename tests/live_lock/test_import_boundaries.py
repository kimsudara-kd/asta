"""Live-lock / boundary proofs (ADR-0002): source scans, so they cannot be satisfied by config."""

import ast
import re
from pathlib import Path

SRC = Path(__file__).parent.parent.parent / "src" / "asta"

BANNED_NAME = re.compile(
    r"(place_(spot_)?order|cancel_order|new_order|submit_order|withdraw|transfer|borrow|"
    r"set_leverage|margin|futures|perpetual|/sapi/|api/v3/order|api/v5/trade|"
    r"X-MBX-APIKEY|OK-ACCESS-KEY|OK-ACCESS-SIGN|hmac)",
    re.IGNORECASE,
)
# the live-lock files themselves must list banned strings to block them
ALLOWED_TO_MENTION = {"egress.py", "settings.py", "gateway.py"}


def py_files():
    return [p for p in SRC.rglob("*.py")]


def test_no_order_or_funds_symbols_in_exchange_code():
    offenders = []
    for p in (SRC / "exchanges").rglob("*.py"):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if BANNED_NAME.search(line) and "NOTE" not in line:
                offenders.append(f"{p.relative_to(SRC)}:{i}: {line.strip()}")
    assert not offenders, offenders


def test_no_signing_or_credentials_anywhere():
    offenders = []
    for p in py_files():
        if p.name in ALLOWED_TO_MENTION:
            continue
        txt = p.read_text(encoding="utf-8")
        if re.search(r"hmac|X-MBX-APIKEY|OK-ACCESS|api_secret|passphrase", txt, re.IGNORECASE):
            offenders.append(str(p.relative_to(SRC)))
    assert not offenders, offenders


def test_no_live_environment_anywhere():
    for p in py_files():
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id.upper() == "LIVE":
                        raise AssertionError(f"LIVE member defined in {p}")


def test_only_gateway_module_is_execution_entrypoint():
    """Nothing outside risk/ may reference the execution-enable constant."""
    allowed = {"risk/gateway.py"}
    for p in py_files():
        rel = p.relative_to(SRC).as_posix()
        if rel in allowed:
            continue
        assert "EXECUTION_ENABLED_IN_THIS_BUILD" not in p.read_text(encoding="utf-8"), rel


def test_module_import_graph():
    """core imports nothing from asta; exchanges only core/exchanges; risk only core/audit."""
    rules = {
        "core": {"core"},
        "audit": {"core", "audit"},
        "risk": {"core", "audit", "risk"},
        "exchanges": {"core", "exchanges"},
        "market_data": {"core", "exchanges", "market_data"},
        "db": {"core", "audit", "db"},
        "auth": {"core", "audit", "auth", "db"},
    }
    bad = []
    for p in py_files():
        top = p.relative_to(SRC).parts[0]
        if top not in rules:
            continue
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            mods = []
            if isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            elif isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            for m in mods:
                parts = m.split(".")
                if parts[0] == "asta" and len(parts) > 1 and parts[1] not in rules[top]:
                    bad.append(f"{p.relative_to(SRC)} imports {m}")
    assert not bad, bad
