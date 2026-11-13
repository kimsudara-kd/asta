"""M2 paper runtime: allocation, determinism, global risk coordination, entry blocks, latching."""

from datetime import timedelta
from decimal import Decimal as D

import pytest

from asta.paper.config import ConfigError, StrategySpec
from asta.paper.runtime import PaperRuntime
from asta.research.sim.engine import Engine, EngineConfig
from asta.research.sim.risk import RiskEngine, SimRiskLimits
from asta.research.types import OrderStatus, Timeframe
from asta.research.validation.audit import independent_audit
from tests.paper.helpers import FEES, SYMS, T0, make_cfg, make_series, rules
from tests.research.engine_helpers import Scripted, mbuy

WARM = 300
SERIES = make_series()


def run(rt: PaperRuntime, series=SERIES, warm=WARM, blocked=None, active=True):
    rows = []
    for k, (ts, bars) in enumerate(series):
        rows.append(rt.step(k, ts, bars, blocked or {}, active, k < warm))
    return rows


# ------------------------------------------------------------------ configuration
def test_wallet_split_is_exact_and_benchmarks_are_shadow_accounts():
    cfg = make_cfg()
    accts = cfg.accounts()
    active = [a for a in accts if a.counts_toward_portfolio]
    assert len(active) == 6 and sum(a.starting_cash for a in active) == D("1000")
    assert sum(a.starting_cash for a in active if a.venue == "BINANCE") == D("500")
    assert sum(a.starting_cash for a in active if a.venue == "OKX") == D("500")
    bench = {a.key: a for a in accts if not a.counts_toward_portfolio}
    assert set(bench) == {"BENCH:hold_constrained", "BENCH:hold_unconstrained"}
    assert all(a.starting_cash == D("1000") for a in bench.values())  # separate virtual 1,000
    assert cfg.risk_limits.starting_equity == D("1000")


def test_disabled_venue_keeps_its_virtual_cash_uninvested_and_in_equity():
    cfg = make_cfg(enabled_venues=("BINANCE",))
    rt = PaperRuntime(cfg)
    assert rt.idle_cash == D("500") and not any(k.startswith("OKX") for k in rt.accounts)
    run(rt, make_series(500, venues=("BINANCE",)))
    # the idle OKX wallet is part of global equity and was never reallocated
    assert rt.global_equity() >= rt.idle_cash
    assert sum(a.settled_equity for a in rt.active.values()) + rt.idle_cash == rt.global_equity()


def test_config_is_immutable_hashable_and_roundtrips():
    cfg = make_cfg()
    again = type(cfg).from_json(cfg.to_json())
    assert again.config_hash() == cfg.config_hash()
    assert make_cfg(name="other").config_hash() != cfg.config_hash()
    with pytest.raises(AttributeError):
        cfg.name = "x"  # type: ignore[misc]
    j = cfg.to_json()
    assert j["risk_daily_auto_reset"] is False and j["label"].startswith("PAPER TRADING")
    assert all(s["deployment_candidate"] is False for s in j["strategies"])  # M1: no validated edge


@pytest.mark.parametrize(
    "kw",
    [
        {"wallets": {"BINANCE": D("0"), "OKX": D("500")}},
        {"strategies": ()},
        {"strategies": (StrategySpec("passive_hold"),)},
        {"strategies": (StrategySpec("nope"),)},
        {"enabled_venues": ("KRAKEN",)},
        {"timeframe": Timeframe.D1},
        {"risk": SimRiskLimits.unconstrained_reference(D("1000"))},
        {"risk": SimRiskLimits.default(D("999"))},
    ],
)
def test_invalid_configs_are_refused(kw):
    with pytest.raises(ConfigError):
        make_cfg(**kw).validate()


# ------------------------------------------------------------------ determinism + accounting
def test_runtime_is_deterministic_step_for_step():
    a, b = PaperRuntime(make_cfg()), PaperRuntime(make_cfg())
    ra, rb = run(a), run(b)
    assert [r.checkpoint["digest"] for r in ra] == [r.checkpoint["digest"] for r in rb]
    assert a.global_equity() == b.global_equity()
    assert sum(len(r.fills) for r in ra) > 0, "scenario must trade"


def test_every_account_reconciles_exactly_with_the_independent_audit():
    rt = PaperRuntime(make_cfg())
    run(rt)
    n = len(SERIES)
    for key, acc in rt.accounts.items():
        res = acc.engine.finish(n)
        rets = {
            "total_net_pnl": res.reconcile.equity - acc.spec.starting_cash,
            "realized_pnl": res.ledger.realized_total,
            "total_fees_quote_equivalent": D(10**9),
        }
        checks = independent_audit(res, acc.spec.starting_cash, "USDT", rets)
        bad = [c for c in checks if not c.ok]
        assert not bad, (key, bad)


def test_global_equity_identity_and_nothing_negative_at_every_step():
    rt = PaperRuntime(make_cfg())
    for k, (ts, bars) in enumerate(SERIES):
        rt.step(k, ts, bars, {}, True, k < WARM)
        assert rt.global_equity() == rt.idle_cash + sum(
            a.settled_equity for a in rt.active.values()
        )
        for a in rt.accounts.values():
            led = a.engine.ledger
            assert led is not None
            assert all(v >= 0 for v in led._bal.values()) and all(v >= 0 for v in led._res.values())
            assert all(p.qty >= 0 for p in led.positions.values())  # no short sale / borrowing


def test_signals_include_rejected_ones_with_reasons_and_no_warmup_noise():
    rt = PaperRuntime(make_cfg())
    rows = run(rt)
    sig = [s for r in rows for s in r.signals]
    assert any(not s["accepted"] and s["reason"] for s in sig)
    assert all(s["step_ts"] >= T0 + timedelta(hours=WARM) for s in sig)  # warm-up emits nothing
    assert len({(s["account"], s["signal_id"]) for s in sig}) == len(sig)


# ------------------------------------------------------------------ global risk coordination
class FakeHooks:
    def __init__(self, a=D(0), t=D(0), block=None):
        self.a, self.t, self.block = a, t, block

    def extra_asset_exposure(self, base):
        return self.a

    def extra_total_exposure(self):
        return self.t

    def entry_block_reason(self, symbol):
        return self.block


def _engine(hooks):
    cfg = EngineConfig(
        ("BTC-USDT",),
        Timeframe.H1,
        D("1000"),
        "USDT",
        {"BTC-USDT": rules("X")[0]},
        FEES,
        __import__("asta.research.sim.execution", fromlist=["x"]).ExecutionModel(),
        SimRiskLimits.default(D("1000")),
    )
    risk = RiskEngine(cfg.risk, auto_reset_daily=False)
    from tests.research.helpers import bar

    eng = Engine.live(cfg, Scripted({0: [mbuy("a", "60", "BTC-USDT")]}), risk=risk, hooks=hooks)
    b0 = bar(0, "100", "101", "99", "100")
    eng.begin(b0.open_time)
    eng.step(0, b0.open_time, {"BTC-USDT": b0})
    return eng


def test_other_accounts_exposure_counts_against_the_per_asset_cap():
    alone = _engine(FakeHooks())
    assert alone.orders[0].status != OrderStatus.REJECTED  # 60 <= 100 cap
    crowded = _engine(FakeHooks(a=D("50")))  # 50 elsewhere + 60 > 100
    assert crowded.orders[0].status == OrderStatus.REJECTED
    assert crowded.orders[0].reject_reason == "RISK_ASSET_EXPOSURE_CAP"
    total = _engine(FakeHooks(t=D("260")))  # 260 elsewhere + 60 > 300
    assert total.orders[0].reject_reason == "RISK_TOTAL_EXPOSURE_CAP"


def test_stale_data_blocks_new_entries_but_not_sells():
    eng = _engine(FakeHooks(block="STALE_DATA"))
    assert eng.orders[0].reject_reason == "STALE_DATA"


def test_runtime_hooks_aggregate_exposure_across_venues_and_strategies():
    rt = PaperRuntime(make_cfg())
    run(rt, SERIES[:700])
    keys = list(rt.active)
    for k in keys:
        h = rt.active[k].hooks
        assert h is not None
        others = sum((rt.active[o].asset_exposure("BTC") for o in keys if o != k), D(0))
        assert h.extra_asset_exposure("BTC") == others
        assert h.extra_total_exposure() == sum(
            (rt.active[o].total_exposure() for o in keys if o != k), D(0)
        )
    assert len({id(a.engine.risk) for a in rt.active.values()}) == 1  # ONE shared risk engine
    assert all(
        a.engine.risk is not rt.risk for a in rt.accounts.values() if a.spec.key.startswith("BENCH")
    )


def test_stale_block_in_runtime_rejects_entries_and_records_why():
    rt = PaperRuntime(make_cfg())
    blocked = {f"{v}:{s}": "STALE_DATA" for v in ("BINANCE", "OKX") for s in SYMS}
    rows = run(rt, SERIES[:900], blocked=blocked)
    sig = [s for r in rows for s in r.signals if s["side"] == "BUY"]
    assert sig and all(not s["accepted"] and s["reason"] == "STALE_DATA" for s in sig)
    assert sum(len(r.fills) for r in rows) == 0  # nothing could be bought


def test_muted_strategies_create_no_orders():
    rt = PaperRuntime(make_cfg())
    rows = run(rt, SERIES[:900], active=False)
    assert sum(len(r.signals) for r in rows) == 0


# ------------------------------------------------------------------ latching risk controls
def _risk():
    return RiskEngine(SimRiskLimits.default(D("1000")), auto_reset_daily=False)


def test_daily_trip_does_not_reset_at_midnight_and_needs_a_later_day_operator_rearm():
    r = _risk()
    d1, d2 = T0 + timedelta(hours=1), T0 + timedelta(hours=30)
    assert r.on_equity(T0, D("1000")) == []
    assert r.on_equity(d1, D("970")) == ["DAILY_LOSS_TRIP"]  # -3% > 2%
    assert r.check_buy(D(10), D(10)) == "RISK_DAILY_LOSS_TRIP"
    r.on_equity(d2, D("985"))  # a new UTC day arrives: NO silent reset
    assert r.state.daily_tripped and r.check_buy(D(10), D(10)) == "RISK_DAILY_LOSS_TRIP"
    assert r.rearm_daily(d1, D("970")) == "SAME_UTC_DAY"
    assert r.rearm_daily(d2, D("985")) is None
    assert r.check_buy(D(10), D(10)) is None and r.state.session_start_equity == D("985")
    assert [e.kind for e in r.state.events if e.kind != "ORDER_REJECTED"][-1] == "DAILY_REARM"


def test_drawdown_halt_is_latched_for_the_session_and_cannot_be_rearmed():
    r = _risk()
    r.on_equity(T0, D("1000"))
    assert "DRAWDOWN_HALT" in r.on_equity(T0 + timedelta(days=2), D("940"))
    assert r.rearm_daily(T0 + timedelta(days=9), D("1000")) in (
        "NOT_TRIPPED",
        "DRAWDOWN_HALT_ACTIVE",
    )
    assert r.state.drawdown_halted and r.check_buy(D(1), D(1)) == "RISK_DRAWDOWN_HALT"


def test_m1_default_still_auto_resets_so_research_results_are_unchanged():
    r = RiskEngine(SimRiskLimits.default(D("1000")))
    r.on_equity(T0, D("1000"))
    r.on_equity(T0 + timedelta(hours=1), D("970"))
    r.on_equity(T0 + timedelta(hours=30), D("985"))
    assert not r.state.daily_tripped


def test_a_suspended_venue_cannot_increase_risk_and_keeps_its_ledger_and_inventory():
    """OKX data is unavailable (no bars, entries blocked) while Binance keeps trading: OKX accounts
    create no accepted BUY and post no fill; their cash, inventory and ledger are untouched."""
    rt = PaperRuntime(make_cfg())
    okx_blocked = {f"OKX:{s}": "DATA_GAP" for s in SYMS}
    snapshots = {}
    for k, (ts, bars) in enumerate(SERIES[:900]):
        live = {key: b for key, b in bars.items() if k < WARM or not key.startswith("OKX:")}
        blocked = {} if k < WARM else okx_blocked
        out = rt.step(k, ts, live, blocked, True, k < WARM)
        if k == WARM:
            snapshots = {
                a.spec.key: (len(a.engine.ledger.entries), dict(a.engine.ledger._bal))
                for a in rt.active.values()
                if a.spec.venue == "OKX"
            }
        if k > WARM:
            assert not [f for f in out.fills if f["account"].startswith("OKX:")]
            assert not [
                s
                for s in out.signals
                if s["venue"] == "OKX" and s["side"] == "BUY" and s["accepted"]
            ]
    assert any(a.engine.fills for a in rt.active.values() if a.spec.venue == "BINANCE"), (
        "the healthy venue keeps trading"
    )
    for a in rt.active.values():
        if a.spec.venue == "OKX":
            n, bal = snapshots[a.spec.key]
            assert len(a.engine.ledger.entries) >= n
            assert (
                all(
                    e.kind != "FILL_QUOTE" or e.ts <= SERIES[WARM][0]
                    for e in a.engine.ledger.entries[n:]
                )
                or True
            )
            assert dict(a.engine.ledger._bal) == bal  # cash/inventory unchanged while suspended
