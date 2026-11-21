"""M3-B step C: the gateway allows exactly ONE cell and nothing else (exhaustive matrix, no database needed).

New file: it ADDS protection and weakens none of the existing live-lock tests. ALLOW requires ALL of: environment EXACTLY
EXCHANGE_DEMO, venue EXACTLY BINANCE_TESTNET, the reviewed constant TESTNET_EXECUTION_ALLOWED, LIVE_TRADING_ENABLED False,
and an active runtime enablement. The shipped constants are all False.
"""

from __future__ import annotations

import ast
import itertools
from decimal import Decimal
from pathlib import Path

import pytest

from asta.risk import gateway
from asta.risk.gateway import Decision, ExecutionGateway, OrderRequest

SRC = Path(__file__).parents[2] / "src" / "asta"


class Enab:
    def __init__(self, state: object) -> None:
        self.state = state

    def active(self, scope: str) -> bool:
        if isinstance(self.state, Exception):
            raise self.state
        return bool(self.state) and scope == "BINANCE_TESTNET"


ENVS = (
    "EXCHANGE_DEMO",
    "PAPER",
    "BACKTEST",
    "LIVE",
    "",
    "exchange_demo",
    " EXCHANGE_DEMO",
    "EXCHANGE_DEMO\n",
)
VENUES = (
    "BINANCE_TESTNET",
    "BINANCE",
    "OKX",
    "binance_testnet",
    " BINANCE_TESTNET",
    "BINANCE_TESTNET\n",
    "",
    "BINANCE_TESTNET2",
)
ENABLEMENTS = (None, True, False, RuntimeError("db down"))


def req(env: str, venue: str, side: str = "BUY", price: Decimal | None = None) -> OrderRequest:
    return OrderRequest(env, venue, "demo", "BTC-USDT", side, Decimal("0.001"), price)


def test_the_shipped_constants_are_all_off():
    assert gateway.EXECUTION_ENABLED_IN_THIS_BUILD is False
    assert gateway.LIVE_TRADING_ENABLED is False
    assert gateway.TESTNET_EXECUTION_ALLOWED is False
    assert (
        gateway.TESTNET_VENUE == "BINANCE_TESTNET"
        and gateway.TESTNET_ENVIRONMENT == "EXCHANGE_DEMO"
    )


def test_as_shipped_every_request_is_denied_whatever_the_enablement_says():
    for env, venue, en in itertools.product(ENVS, VENUES, ENABLEMENTS):
        d = ExecutionGateway(None if en is None else Enab(en)).authorize(req(env, venue))
        assert d.decision == Decision.DENY, (env, venue, en)
        assert "M0_EXECUTION_DISABLED" in d.reason_codes


@pytest.mark.parametrize("exec_flag", [False, True])
@pytest.mark.parametrize("testnet_flag", [False, True])
@pytest.mark.parametrize("live_flag", [False, True])
def test_exactly_one_cell_is_ever_allowed(monkeypatch, exec_flag, testnet_flag, live_flag):
    monkeypatch.setattr(gateway, "EXECUTION_ENABLED_IN_THIS_BUILD", exec_flag)
    monkeypatch.setattr(gateway, "TESTNET_EXECUTION_ALLOWED", testnet_flag)
    monkeypatch.setattr(gateway, "LIVE_TRADING_ENABLED", live_flag)
    allowed = []
    for env, venue, en, side, price in itertools.product(
        ENVS, VENUES, ENABLEMENTS, ("BUY", "SELL"), (None, Decimal("50000"))
    ):
        d = ExecutionGateway(None if en is None else Enab(en)).authorize(
            req(env, venue, side, price)
        )
        want = (
            env == "EXCHANGE_DEMO"
            and venue == "BINANCE_TESTNET"
            and testnet_flag
            and not live_flag
            and en is True
        )
        assert (d.decision == Decision.ALLOW) is want, (
            env,
            venue,
            en,
            exec_flag,
            testnet_flag,
            live_flag,
        )
        if d.decision == Decision.ALLOW:
            allowed.append((env, venue))
            assert d.reason_codes == ("TESTNET_ONLY",)
        else:
            assert d.reason_codes  # a denial always says why
    assert set(allowed) <= {("EXCHANGE_DEMO", "BINANCE_TESTNET")}


def test_a_failing_or_missing_enablement_source_is_a_refusal_with_a_reason(monkeypatch):
    monkeypatch.setattr(gateway, "TESTNET_EXECUTION_ALLOWED", True)
    for en in (None, Enab(False), Enab(RuntimeError("boom"))):
        d = ExecutionGateway(en).authorize(req("EXCHANGE_DEMO", "BINANCE_TESTNET"))
        assert d.decision == Decision.DENY and "TESTNET_NOT_ENABLED" in d.reason_codes


def test_the_general_execution_flag_alone_never_opens_anything_but_the_testnet_cell(monkeypatch):
    monkeypatch.setattr(gateway, "EXECUTION_ENABLED_IN_THIS_BUILD", True)
    for venue in ("BINANCE", "OKX", "BINANCE_TESTNET"):
        d = ExecutionGateway(Enab(True)).authorize(req("EXCHANGE_DEMO", venue))
        assert d.decision == Decision.DENY  # the testnet constant is still False


def test_the_gateway_module_cannot_be_configured_from_the_outside():
    tree = ast.parse((SRC / "risk/gateway.py").read_text())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    }
    assert not names & {"environ", "getenv", "settings", "load_settings", "open", "os", "sys"}
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert imported <= {
        "__future__",
        "dataclasses",
        "decimal",
        "enum",
        "typing",
        "asta.core.environment",
    }


def test_only_the_gateway_module_names_the_execution_constants():
    for p in SRC.rglob("*.py"):
        if p.relative_to(SRC).as_posix() == "risk/gateway.py":
            continue
        text = p.read_text(encoding="utf-8")
        for name in ("EXECUTION_ENABLED_IN_THIS_BUILD", "TESTNET_EXECUTION_ALLOWED"):
            assert name not in text, (p.relative_to(SRC), name)


def test_the_enablement_view_only_asks():
    tree = ast.parse((SRC / "orders/enablement.py").read_text(encoding="utf-8"))
    sql = [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and any(k in n.value.upper() for k in ("SELECT", "INSERT", "UPDATE", "DELETE"))
        and "``" not in n.value  # the module docstring names the enable function in prose
        and len(n.value) < 80
    ]
    assert sql == ["SELECT execution_enabled(:s)"]


@pytest.mark.parametrize("testnet_flag", [False, True])
@pytest.mark.parametrize("live_flag", [False, True])
def test_reducing_operations_need_the_cell_and_the_constant_but_never_the_enablement(
    monkeypatch, testnet_flag, live_flag
):
    monkeypatch.setattr(gateway, "TESTNET_EXECUTION_ALLOWED", testnet_flag)
    monkeypatch.setattr(gateway, "LIVE_TRADING_ENABLED", live_flag)
    for env, venue, en in itertools.product(ENVS, VENUES, ENABLEMENTS):
        d = ExecutionGateway(None if en is None else Enab(en)).authorize_reducing(req(env, venue))
        want = (
            env == "EXCHANGE_DEMO" and venue == "BINANCE_TESTNET" and testnet_flag and not live_flag
        )
        assert (d.decision == Decision.ALLOW) is want, (env, venue, en, testnet_flag, live_flag)
        if not want:
            assert d.reason_codes


def test_as_shipped_even_reducing_operations_are_denied():
    for env, venue in itertools.product(ENVS, VENUES):
        assert ExecutionGateway().authorize_reducing(req(env, venue)).decision == Decision.DENY
