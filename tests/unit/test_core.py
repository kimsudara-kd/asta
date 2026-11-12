from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from asta.core.egress import EgressDenied, assert_url_allowed
from asta.core.environment import Environment
from asta.core.money import floor_to_step, loads_decimal, to_decimal
from asta.core.settings import CredentialsPresent, assert_no_exchange_credentials
from asta.core.timeutil import ClockMonitor, require_utc


def test_environment_members_exact_and_no_live():
    assert {e.value for e in Environment} == {"BACKTEST", "PAPER", "EXCHANGE_DEMO"}
    with pytest.raises(ValueError):
        Environment("LIVE")


def test_to_decimal_rejects_float_and_bool():
    with pytest.raises(TypeError):
        to_decimal(0.1)
    with pytest.raises(TypeError):
        to_decimal(True)
    assert to_decimal("0.1") + to_decimal("0.2") == Decimal("0.3")
    with pytest.raises(ValueError):
        to_decimal("NaN")


def test_json_numbers_parse_as_decimal():
    assert loads_decimal('{"p": 0.1, "q": 2}')["p"] == Decimal("0.1")


@given(
    st.decimals(min_value=0, max_value=10**9, places=8),
    st.decimals(min_value=Decimal("0.00000001"), max_value=1000, places=8),
)
def test_floor_to_step_never_rounds_up(value, step):
    out = floor_to_step(value, step)
    assert out <= value
    assert value - out < step
    assert out % step == 0


def test_require_utc():
    require_utc(datetime.now(UTC))
    with pytest.raises(ValueError):
        require_utc(datetime.now())  # noqa: DTZ005
    with pytest.raises(ValueError):
        require_utc(datetime.now(UTC).astimezone(timezone_plus1()))


def timezone_plus1():
    from datetime import timezone

    return timezone(timedelta(hours=1))


# ---- clock drift ----
def test_clock_drift_ok_and_exceeded_and_unknown():
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    mon = ClockMonitor(max_drift=timedelta(seconds=2), now=lambda: t0)
    assert not mon.is_drift_ok("BINANCE")  # unknown -> fail closed
    mon.record("BINANCE", t0, t0 + timedelta(milliseconds=50), t0 + timedelta(milliseconds=100))
    assert mon.is_drift_ok("BINANCE")
    mon.record("OKX", t0, t0 + timedelta(seconds=5), t0 + timedelta(milliseconds=100))
    assert not mon.is_drift_ok("OKX")
    mon.record("OKX", t0, t0 - timedelta(seconds=5), t0 + timedelta(milliseconds=100))
    assert not mon.is_drift_ok("OKX")  # negative drift also trips


# ---- egress ----
@pytest.mark.parametrize("url", [
    "https://data-api.binance.vision/api/v3/time",
    "wss://data-stream.binance.vision/stream?streams=btcusdt@trade",
    "https://www.okx.com/api/v5/public/time",
    "wss://ws.okx.com:8443/ws/v5/public",
])
def test_egress_allows_public(url):
    assert assert_url_allowed(url) == url


@pytest.mark.parametrize("url", [
    "http://data-api.binance.vision/api/v3/time",          # not TLS
    "https://api.binance.com/api/v3/time",                 # host not allowlisted
    "https://evil.example.com/x",
    "https://data-api.binance.vision/api/v3/order",        # trading path
    "https://testnet.binance.vision/api/v3/account",
    "https://data-api.binance.vision/sapi/v1/capital/withdraw/apply",
    "https://www.okx.com/api/v5/trade/order",
    "https://www.okx.com/api/v5/asset/withdrawal",
    "https://www.okx.com/api/v5/account/set-leverage",
    "wss://ws.okx.com:8443/ws/v5/private",
    "https://user:pw@www.okx.com/api/v5/public/time",
    "ftp://www.okx.com/x",
])
def test_egress_denies(url):
    with pytest.raises(EgressDenied):
        assert_url_allowed(url)


# ---- credentials guard ----
def test_credentials_env_refused():
    with pytest.raises(CredentialsPresent):
        assert_no_exchange_credentials({"BINANCE_API_KEY": "x"})
    with pytest.raises(CredentialsPresent):
        assert_no_exchange_credentials({"OKX_PASSPHRASE": "x"})
    assert_no_exchange_credentials({"PATH": "x", "ASTA_DATABASE_URL": "y"})
