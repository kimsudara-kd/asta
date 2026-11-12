"""M2 live-lock regression: PAPER execution is physically separate from every exchange client.

* asta.paper (simulator, store, worker, API views) imports no exchange adapter and no network or
  process library; asta.exchanges never imports asta.paper.
* Nothing in M2 can sign, authenticate or submit an order: no credential/signature vocabulary in
  the paper code, the gateway stays default-deny, Environment still has no LIVE.
* The outbound-call composition root (paper_service) only uses the egress-checked public adapters.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "asta"
PAPER = sorted((SRC / "paper").glob("*.py"))
BANNED_IN_PAPER = {
    "asta.exchanges",
    "httpx",
    "websockets",
    "aiohttp",
    "requests",
    "urllib.request",
    "http.client",
    "socket",
    "ssl",
    "subprocess",
    "ftplib",
    "smtplib",
    "telnetlib",
    "xmlrpc",
    "grpc",
}
AUTH_VOCAB = (
    "hmac",
    "api_key",
    "apikey",
    "api-key",
    "secret_key",
    "passphrase",
    "x-mbx-apikey",
    "ok-access",
    "signature",
    "private_key",
    "listenkey",
    "sign_request",
    "api_secret",
)


def imports(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out |= {f"{node.module}.{a.name}" for a in node.names}
    return out


def test_paper_package_has_files_to_check():
    assert len(PAPER) >= 10


def test_paper_code_imports_no_exchange_adapter_and_no_network_or_process_library():
    for f in PAPER:
        bad = {
            i for i in imports(f) if any(i == b or i.startswith(b + ".") for b in BANNED_IN_PAPER)
        }
        assert not bad, (f.name, bad)


def test_exchange_adapters_never_import_the_paper_package():
    for f in (SRC / "exchanges").rglob("*.py"):
        assert not any(i.startswith("asta.paper") for i in imports(f)), f


def test_the_paper_api_module_imports_no_exchange_adapter_either():
    bad = {i for i in imports(SRC / "api" / "paper.py") if i.startswith("asta.exchanges")}
    assert not bad, bad


def test_no_credential_or_signing_vocabulary_in_any_m2_module():
    files = [*PAPER, SRC / "api" / "paper.py", SRC / "paper_service.py"]
    for f in files:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        names = {n.id.lower() for n in ast.walk(tree) if isinstance(n, ast.Name)}
        names |= {n.attr.lower() for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        names |= {
            a.arg.lower() for n in ast.walk(tree) if isinstance(n, ast.arguments) for a in n.args
        }
        strings = {
            n.value.lower()
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        }
        for token in AUTH_VOCAB:
            assert not any(token in n for n in names), (f.name, token)
            # docstrings may *deny* these things; executable string constants must not name them
            assert not any(token in s and len(s) < 60 for s in strings), (f.name, token)


def test_service_uses_only_the_existing_egress_checked_public_adapters():
    imp = imports(SRC / "paper_service.py")
    assert {"asta.exchanges.binance.adapter", "asta.exchanges.okx.adapter"} <= imp
    assert not {i for i in imp if i in ("httpx", "websockets", "requests", "aiohttp")}
    assert not any(
        i.startswith("asta.exchanges")
        and i
        not in (
            "asta.exchanges.binance.adapter",
            "asta.exchanges.okx.adapter",
            "asta.exchanges.common.base",
            "asta.exchanges.common.ws",
            "asta.exchanges.binance.adapter.BinancePublicAdapter",
            "asta.exchanges.okx.adapter.OkxPublicAdapter",
            "asta.exchanges.common.base.ALLOWLIST_SYMBOLS",
            "asta.exchanges.common.base.BINANCE_ALLOWLIST",
            "asta.exchanges.common.ws.Reconnected",
        )
        for i in imp
    )


def test_gateway_environment_and_egress_are_unchanged_by_m2():
    from asta.core import egress
    from asta.core.environment import Environment
    from asta.risk import gateway

    assert (
        gateway.EXECUTION_ENABLED_IN_THIS_BUILD is False and gateway.LIVE_TRADING_ENABLED is False
    )
    assert {e.value for e in Environment} == {"BACKTEST", "PAPER", "EXCHANGE_DEMO"}
    assert "/ws/v5/business" in egress.FORBIDDEN_PATH_FRAGMENTS  # private/business WS stays denied
    assert (
        "/api/v5/trade" in egress.FORBIDDEN_PATH_FRAGMENTS
        and "/api/v3/order" in egress.FORBIDDEN_PATH_FRAGMENTS
    )
    req = gateway.OrderRequest(
        "PAPER", "BINANCE", "a", "BTC-USDT", "BUY", __import__("decimal").Decimal(1)
    )
    assert gateway.ExecutionGateway().authorize(req).decision == gateway.Decision.DENY


def test_paper_session_states_have_no_transition_to_an_external_effect():
    from asta.paper.states import TRANSITIONS, SessionState

    assert set(TRANSITIONS) == set(SessionState)
    assert not TRANSITIONS[SessionState.STOPPED] and not TRANSITIONS[SessionState.FAILED]
