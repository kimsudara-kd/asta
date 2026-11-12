"""Hand-calculated golden cases (every expected number is worked out in the docstrings)."""

from dataclasses import FrozenInstanceError
from datetime import timedelta
from decimal import Decimal as D

import pytest

from asta.research.sim.execution import ExecutionModel
from asta.research.sim.ledger import Ledger, LedgerError
from asta.research.types import OrderStatus

from .engine_helpers import cancel, lbuy, lsell, mbuy, mbuy_qty, msell, run, stop
from .helpers import T0, bar


def status_of(res, cid):
    o = next(o for o in res.orders if o.client_id == cid)
    return o.status, o.reject_reason or o.events[-1].reason


def test_golden_market_round_trip_fees_slippage_pnl():
    """
    bar0 O100 C100: decide BUY 500 USDT (ref 100 -> qty 5.000).
    bar1 O100: market fill, slip 10 bps -> 100.10; notional round_up(100.10*5)=500.50;
               fee (received asset) round_up(5*10/10000)=0.005 base -> received 4.995; cash 499.50.
    bar1 close: decide SELL 4.995.
    bar2 O110: slip 10 bps -> 109.89; notional round_down(109.89*4.995)=548.90055;
               fee round_up(548.90055*10/10000)=0.54890055; cash_in 548.35164945.
    final cash = 499.50 + 548.35164945 = 1047.85164945; cost relieved 500.50; realized 47.85164945.
    """
    bars = [
        bar(0, "100", "101", "99", "100"),
        bar(1, "100", "105", "99", "104"),
        bar(2, "110", "112", "108", "110"),
    ]
    res = run(bars, {0: [mbuy("b1")], 1: [msell("s1", "4.995")]})
    assert [f.price for f in res.fills] == [D("100.10"), D("109.89")]
    buy, sell = res.fills
    assert buy.quantity == D("5.000") and buy.notional == D("500.50")
    assert (buy.fee_asset, buy.fee_amount) == ("BTC", D("0.00500000"))
    assert sell.quantity == D("4.995") and sell.notional == D("548.90055000")
    assert (sell.fee_asset, sell.fee_amount) == ("USDT", D("0.54890055"))
    assert sell.realized_pnl == D("47.85164945")
    assert buy.slippage_cost == D("0.50000000") and sell.slippage_cost == D("0.54945000")
    led = res.ledger
    assert led.balance("USDT") == D("1047.85164945") and led.balance("BTC") == 0
    assert led.reserved("USDT") == 0 and led.reserved("BTC") == 0
    assert led.realized_total == D("47.85164945")
    assert res.equity[-1].equity == D("1047.85164945")
    assert res.reconcile.ok, res.reconcile.issues
    assert res.reconcile.unrealized == 0
    assert status_of(res, "b1")[0] == OrderStatus.FILLED
    assert status_of(res, "s1")[0] == OrderStatus.FILLED


def test_golden_open_position_marked_to_market_not_realised():
    """Buy at 100.10 and hold; final close 104: equity = cash + qty*mark, NOT a cash profit.
    unrealized = 4.995*104 - 500.50 = 519.48 - 500.50 = 18.98"""
    bars = [bar(0, "100", "101", "99", "100"), bar(1, "100", "105", "99", "104")]
    res = run(bars, {0: [mbuy("b1")]})
    led = res.ledger
    assert led.balance("USDT") == D("499.50") and led.position("BTC").qty == D("4.995000")
    assert res.reconcile.unrealized == D("18.98000000") and led.realized_total == 0
    assert res.equity[-1].equity == D("499.50") + D("519.48")
    assert res.reconcile.ok
    # the conservative liquidation value is below the mark-to-market (exit fee + slippage)
    assert res.liquidation_equity < res.equity[-1].equity


def test_limit_order_requires_trade_through_not_touch():
    bars = [
        bar(0, "100", "101", "99", "100"),
        bar(1, "100", "105", "99", "104"),  # low touches 99.00 exactly: NOT a fill
        bar(2, "104", "105", "98.9", "100"),  # low 98.90 <= 99 - 1 tick (98.99): fill
    ]
    res = run(bars, {0: [lbuy("l1", "99", "2")]})
    assert [(f.liquidity, f.price, f.ts) for f in res.fills] == [
        ("MAKER", D("99.00"), bars[2].open_time)
    ]
    f = res.fills[0]
    assert f.slippage_cost == 0  # a resting maker fill carries no slippage
    assert f.fee_asset == "BTC" and f.fee_amount == D("0.002")  # 2 * 10 bps


def test_limit_marketable_at_open_fills_as_taker_capped_by_the_limit():
    bars = [bar(0, "100", "101", "99", "100"), bar(1, "100", "102", "99", "101")]
    res = run(bars, {0: [lbuy("l1", "101", "1")]})  # open 100 <= limit 101: marketable
    (f,) = res.fills
    assert f.liquidity == "TAKER" and f.price == D("100.10")  # min(limit, open + slippage)
    res2 = run(bars, {0: [lbuy("l2", "100.05", "1")]})
    assert res2.fills[0].price == D("100.05")  # the limit caps the taker price


def test_unfilled_limit_creates_no_pnl_or_inventory():
    bars = [bar(i, "100", "101", "99.5", "100") for i in range(6)]
    res = run(bars, {0: [lbuy("l1", "90", "2", expire_after_bars=3)]})
    assert res.fills == [] and res.ledger.balance("BTC") == 0
    assert res.ledger.balance("USDT") == D("1000") and res.ledger.reserved("USDT") == 0
    assert status_of(res, "l1")[0] == OrderStatus.EXPIRED
    assert res.equity[-1].equity == D("1000")


def test_partial_fills_follow_volume_cap_and_continue():
    ex = ExecutionModel("cap", D("0"), D("0"), D("0"), D("0.5"), D("1"), 1, D("2"), D("150"))
    bars = [bar(i, "100", "101", "99", "100", v="10") for i in range(4)]
    res = run(bars, {0: [mbuy_qty("m1", "8")]}, execution=ex)  # qty 8, cap per bar = 10*0.5 = 5
    assert [f.quantity for f in res.fills] == [D("5.000"), D("3.000")]
    o = res.orders[0]
    assert o.status == OrderStatus.FILLED
    assert [e.status for e in o.events][-3:] == [
        OrderStatus.SIMULATED_OPEN,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
    ]
    assert res.reconcile.ok


def test_cancel_releases_reservation_and_leaves_no_trace():
    bars = [bar(i, "100", "101", "99.5", "100") for i in range(5)]
    res = run(bars, {0: [lbuy("l1", "90", "2")], 1: [cancel("l1")]})
    assert status_of(res, "l1")[0] == OrderStatus.CANCELLED
    assert res.ledger.reserved("USDT") == 0 and res.fills == []
    kinds = [e.kind for e in res.ledger.entries]
    assert kinds.count("RESERVE") == 1 and kinds.count("RELEASE") == 1


def test_min_qty_min_notional_and_lot_step_rejections():
    bars = [bar(i, "100", "101", "99", "100") for i in range(3)]
    res = run(
        bars,
        {
            0: [
                lbuy("tiny", "100", "0.0004"),
                lbuy("dust", "100", "0.04"),
                lbuy("ok", "100", "0.05"),
            ]
        },
    )
    assert status_of(res, "tiny") == (OrderStatus.REJECTED, "MIN_QTY")
    assert status_of(res, "dust") == (OrderStatus.REJECTED, "MIN_NOTIONAL")  # 0.04*100 = 4 < 5
    assert status_of(res, "ok")[0] != OrderStatus.REJECTED
    res2 = run(bars, {0: [lbuy("lot", "100", "0.0509")]})
    assert res2.orders[0].quantity == D("0.050")  # floors to the lot step, never rounds up


def test_insufficient_cash_and_inventory_rejected_never_negative():
    bars = [bar(i, "100", "101", "99", "100") for i in range(3)]
    res = run(bars, {0: [mbuy("big", "5000")], 1: [msell("short", "1")]})
    assert status_of(res, "big") == (OrderStatus.REJECTED, "INSUFFICIENT_CASH")
    assert status_of(res, "short") == (
        OrderStatus.REJECTED,
        "INSUFFICIENT_INVENTORY",
    )  # no shorting
    assert res.ledger.balance("USDT") == D("1000") and res.ledger.balance("BTC") == 0


def test_open_buy_reservations_reduce_available_cash():
    bars = [bar(i, "100", "101", "99.5", "100") for i in range(4)]
    res = run(bars, {0: [lbuy("a", "90", "8"), lbuy("b", "90", "8")]})  # 720 each; cash 1000
    assert status_of(res, "a")[0] != OrderStatus.REJECTED
    assert status_of(res, "b") == (OrderStatus.REJECTED, "INSUFFICIENT_CASH")


def test_inventory_bought_in_a_bar_cannot_be_sold_in_the_same_decision():
    """No intrabar round trips: a sell needs inventory that is settled at DECISION time."""
    bars = [
        bar(0, "100", "101", "99", "100"),
        bar(1, "100", "120", "80", "100"),
        bar(2, "100", "101", "99", "100"),
    ]
    res = run(bars, {0: [mbuy("b1", "300"), lsell("tp", "110", "2.9")]})
    assert status_of(res, "tp") == (OrderStatus.REJECTED, "INSUFFICIENT_INVENTORY")


def test_stop_gap_down_fills_at_open_with_extra_slippage_and_not_at_the_stop():
    bars = [
        bar(0, "100", "101", "99", "100"),
        bar(1, "100", "101", "99", "100"),
        bar(2, "90", "91", "85", "88"),  # gaps below the 95 stop
    ]
    res = run(bars, {0: [mbuy("b1", "300")], 1: [stop("sl", "95", "2.997")]})
    sl = next(f for f in res.fills if f.side.value == "SELL")
    # open 90, stop slippage = 10 bps * 2 = 20 bps -> 90 * (1 - 0.002) = 89.82
    assert sl.price == D("89.82") and sl.price < D("95")
    assert sl.reference_price == D("90")
    assert res.reconcile.ok


def test_oco_stop_wins_when_both_levels_trade_in_the_same_bar_and_pool_is_released():
    bars = [
        bar(0, "100", "101", "99", "100"),
        bar(1, "100", "101", "99", "100"),
        bar(2, "100", "120", "80", "100"),  # both TP 110 and stop 90 lie inside the range
    ]
    res = run(
        bars,
        {
            0: [mbuy("b1", "300")],
            1: [
                lsell("tp", "110", "2.997", oco_group="g"),
                stop("sl", "90", "2.997", oco_group="g"),
            ],
        },
    )
    sells = [f for f in res.fills if f.side.value == "SELL"]
    assert len(sells) == 1
    assert status_of(res, "sl")[0] == OrderStatus.FILLED
    assert status_of(res, "tp")[0] == OrderStatus.CANCELLED
    assert sells[0].price < D("90")  # the adverse outcome was chosen, not the profitable one
    assert res.ledger.reserved("BTC") == 0 and res.reconcile.ok


def test_warmup_intents_are_rejected_and_counted():
    bars = [bar(i, "100", "101", "99", "100") for i in range(6)]
    res = run(bars, {1: [mbuy("w1")], 4: [mbuy("t1", "100")]}, trade_start=T0 + timedelta(hours=3))
    assert status_of(res, "w1") == (OrderStatus.REJECTED, "WARMUP")
    assert res.counters["warmup_dropped"] == 1
    assert res.warmup_bars == 3 and len(res.equity) == 3  # equity recorded only after trade_start
    assert any(f.side.value == "BUY" for f in res.fills)


def test_data_gap_expires_pending_orders_without_synthetic_fills():
    # bars 0,1 then a 3-bar hole, then bars 5,6 where price would have crossed the limit
    bars = [
        bar(0, "100", "101", "99", "100"),
        bar(1, "100", "101", "99", "100"),
        bar(5, "100", "101", "80", "95"),
        bar(6, "95", "96", "79", "90"),
    ]
    res = run(bars, {1: [lbuy("l1", "90", "2", expire_after_bars=10)]})
    assert status_of(res, "l1") == (OrderStatus.EXPIRED, "GAP")
    assert res.fills == [] and res.counters["gap_expiries"] == 1


def test_duplicate_client_id_is_idempotent():
    bars = [bar(i, "100", "101", "99", "100") for i in range(4)]
    res = run(bars, {0: [mbuy("same", "100")], 1: [mbuy("same", "100")], 2: [mbuy("same", "100")]})
    assert len(res.orders) == 1 and res.counters["duplicate_ignored"] == 2
    assert len([f for f in res.fills if f.side.value == "BUY"]) == 1


def test_latency_bars_delay_activation():
    bars = [bar(i, "100", "101", "99", "100") for i in range(5)]
    base = run(bars, {0: [mbuy("m", "100")]})
    slow = run(bars, {0: [mbuy("m", "100")]}, latency_bars=2)
    assert base.fills[0].ts == bars[1].open_time and slow.fills[0].ts == bars[3].open_time


def test_end_of_data_cancels_open_orders_and_releases():
    bars = [bar(i, "100", "101", "99.5", "100") for i in range(3)]
    res = run(bars, {2: [lbuy("late", "90", "2", expire_after_bars=50)]})
    assert status_of(res, "late") == (OrderStatus.CANCELLED, "END_OF_DATA")
    assert res.ledger.reserved("USDT") == 0


def test_ledger_entries_are_immutable_and_corrections_are_compensating():
    led = Ledger.open("USDT", D("1000"), T0)
    e = led.entries[0]
    with pytest.raises(FrozenInstanceError):
        e.amount = D("999999")  # type: ignore[misc]
    led.correct_cash(T0, D("-1.5"), "fee rebate error")
    assert led.balance("USDT") == D("998.5") and led.entries[-1].kind == "CORRECTION"
    assert led.entries[0].amount == D("1000")  # history untouched
    rec = led.reconcile({})
    assert rec.ok and rec.equity - led.initial_cash == led.corrections_total
    with pytest.raises(LedgerError):
        led.correct_cash(T0, D("-5000"), "would go negative")
    with pytest.raises(LedgerError):
        led.apply_sell(T0, "BTC", D("1"), D("100"), D("10"), 1, 1)  # no inventory => no short
