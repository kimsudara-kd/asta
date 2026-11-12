from decimal import Decimal as D

import pytest

from asta.core.environment import Environment
from asta.risk.gateway import (
    LIVE_TRADING_ENABLED,
    Decision,
    EnvironmentNotAllowed,
    ExecutionGateway,
    OrderRequest,
)
from asta.risk.policy import RiskPolicy, RiskSnapshot, evaluate


def _req(env: str) -> OrderRequest:
    return OrderRequest(env, "BINANCE", "acct", "BTC-USDT", "BUY", D("0.001"), D("50000"))


@pytest.mark.parametrize("env", ["BACKTEST", "PAPER", "EXCHANGE_DEMO", "LIVE", "", "paper"])
def test_gateway_denies_everything_in_m0(env):
    d = ExecutionGateway().authorize(_req(env))
    assert d.decision is Decision.DENY
    assert "M0_EXECUTION_DISABLED" in d.reason_codes


def test_gateway_live_constant_false_and_env_var_cannot_flip(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("ASTA_LIVE_TRADING_ENABLED", "true")
    assert LIVE_TRADING_ENABLED is False
    assert ExecutionGateway().authorize(_req("EXCHANGE_DEMO")).decision is Decision.DENY


def test_environment_check():
    g = ExecutionGateway()
    assert g.assert_environment_allowed("PAPER") is Environment.PAPER
    with pytest.raises(EnvironmentNotAllowed):
        g.assert_environment_allowed("BACKTEST")
    with pytest.raises(EnvironmentNotAllowed):
        g.assert_environment_allowed("LIVE")


def _snap(**kw):
    base = dict(equity=D("1000"), start_of_day_equity=D("1000"), peak_equity=D("1000"),
                invested_by_asset={"BTC": D("50")}, cash=D("950"),
                data_fresh=True, clock_ok=True, reconciled=True)
    base.update(kw)
    return RiskSnapshot(**base)


def test_risk_ok():
    assert not evaluate(RiskPolicy(), _snap()).halt_new_buys


@pytest.mark.parametrize("kw,reason", [
    (dict(data_fresh=False), "STALE_MARKET_DATA"),
    (dict(clock_ok=False), "CLOCK_DRIFT"),
    (dict(reconciled=False), "UNRECONCILED"),
    (dict(equity=D("980")), "DAILY_DRAWDOWN_TRIP"),
    (dict(equity=D("949"), start_of_day_equity=D("949")), "PEAK_DRAWDOWN_TRIP"),
    (dict(invested_by_asset={"BTC": D("100"), "ETH": D("100"), "SOL": D("101")}, cash=D("699")),
     "TOTAL_EXPOSURE_CAP"),
    (dict(invested_by_asset={"BTC": D("101")}), "ASSET_EXPOSURE_CAP"),
    (dict(cash=D("600")), "CASH_RESERVE"),
    (dict(cash=D("-1")), "NEGATIVE_BALANCE_INVARIANT"),
    (dict(invested_by_asset={"A": D("1"), "B": D("1"), "C": D("1"), "D": D("1")}),
     "MAX_POSITIONS"),
])
def test_risk_trips(kw, reason):
    ev = evaluate(RiskPolicy(), _snap(**kw))
    assert ev.halt_new_buys and reason in ev.reasons
