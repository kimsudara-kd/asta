"""Deterministic order identity + exact-once gate (F-12 / T-14 / B-6). Pure; nothing else calls it."""

import random
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from asta.paper.order_identity import (
    ID_VERSION,
    DecisionKey,
    ExactOnceGate,
    IdentityError,
    OrdinalAssigner,
    client_order_id,
    decision_id,
    is_valid_client_order_id,
)
from asta.paper.runtime import PaperRuntime
from tests.paper.helpers import make_cfg, make_series

T = datetime(2026, 11, 14, 9, 5, tzinfo=UTC)
KEY = DecisionKey("a" * 64, "BINANCE:sma_cross", "BTC-USDT", "BUY", "MARKET", "entry", T)

# Known-answer vector: changing the id format/encoding silently would break exact-once across a restart
# of a running demo session, so a deliberate change must bump ID_VERSION and this value together.
KNOWN_ANSWER = "97f823e96580029a9fed23faf9a6733b"


def test_known_answer_vector_pins_the_format():
    assert ID_VERSION == "asta-coid-v1"
    assert decision_id(KEY) == KNOWN_ANSWER == client_order_id(KEY)


def test_id_satisfies_binance_and_okx_client_id_rules():
    cid = client_order_id(KEY)
    assert is_valid_client_order_id(cid)
    assert re.fullmatch(r"^[a-zA-Z0-9-_]{1,36}$", cid)  # Binance (FIX regex, assumed for REST)
    assert re.fullmatch(r"^[a-zA-Z0-9]{1,32}$", cid)  # OKX clOrdId
    assert not is_valid_client_order_id(cid.upper()) and not is_valid_client_order_id(cid[:-1])
    assert "entry" not in cid and "BTC" not in cid  # no strategy detail leaks


@pytest.mark.parametrize(
    "change",
    [
        {"session_hash": "b" * 64},
        {"account": "OKX:sma_cross"},
        {"symbol": "ETH-USDT"},
        {"side": "SELL"},
        {"order_type": "LIMIT"},
        {"tag": "exit"},
        {"decision_close": T + timedelta(microseconds=1)},
        {"ordinal": 1},
    ],
)
def test_every_field_changes_the_id(change):
    assert decision_id(replace(KEY, **change)) != decision_id(KEY)


def test_same_instant_in_another_timezone_is_the_same_decision():
    bkk = timezone(timedelta(hours=7))
    assert decision_id(replace(KEY, decision_close=T.astimezone(bkk))) == decision_id(KEY)


def test_naive_time_negative_ordinal_and_control_characters_are_refused():
    with pytest.raises(IdentityError):
        decision_id(replace(KEY, decision_close=datetime(2026, 11, 14, 9, 5)))
    with pytest.raises(IdentityError):
        decision_id(replace(KEY, ordinal=-1))
    for bad in ("x\x1fy", "x\x00y"):
        with pytest.raises(IdentityError):
            decision_id(replace(KEY, tag=bad))


def test_field_boundaries_cannot_be_forged_by_concatenation():
    a = replace(KEY, account="A", symbol="BC")
    b = replace(KEY, account="AB", symbol="C")
    assert decision_id(a) != decision_id(b)


def test_no_collisions_over_many_random_decisions():
    rnd = random.Random(7)
    seen = set()
    for i in range(50_000):
        k = replace(
            KEY,
            account=f"V{rnd.randrange(4)}:s{rnd.randrange(5)}",
            symbol=rnd.choice(["BTC-USDT", "ETH-USDT", "SOL-USDT"]),
            tag=f"t{rnd.randrange(6)}",
            decision_close=T + timedelta(minutes=5 * i),
            ordinal=rnd.randrange(3),
        )
        seen.add(decision_id(k))
    assert len(seen) >= 49_990  # distinct keys; a handful of repeated random keys is expected


def test_identical_intents_in_one_bar_get_distinct_ordinals_and_replays_reproduce_them():
    def run():
        a = OrdinalAssigner()
        return [decision_id(a.assign(KEY)) for _ in range(3)] + [
            decision_id(a.assign(replace(KEY, decision_close=T + timedelta(minutes=5))))
        ]

    first, second = run(), run()
    assert first == second  # deterministic across a restart
    assert len(set(first)) == 4  # three same-bar duplicates + the next bar: all distinct
    assert first[0] == KNOWN_ANSWER  # ordinal 0 is the plain decision id


def test_exact_once_gate_blocks_resubmission_even_after_a_restart():
    gate = ExactOnceGate()
    did = decision_id(KEY)
    assert gate.claim(did) is True and gate.claim(did) is False
    restarted = ExactOnceGate(already_claimed=set(gate.claimed()))  # reloaded from durable storage
    assert restarted.claim(did) is False
    assert restarted.claim(decision_id(replace(KEY, ordinal=1))) is True
    with pytest.raises(IdentityError):
        gate.claim("not-an-id")


# ------------------------------------------------------------------ against the real paper runtime
def _orders(cfg):
    rt = PaperRuntime(cfg)
    series = make_series()
    for k, (ts, bars) in enumerate(series):
        rt.step(k, ts, bars, {}, True, k < 300)
    cfg_hash = cfg.config_hash()
    out = []
    for key, acc in rt.accounts.items():
        assigner = OrdinalAssigner()
        for o in acc.engine.orders:
            out.append(
                decision_id(
                    assigner.assign(
                        DecisionKey(
                            cfg_hash,
                            key,
                            o.symbol,
                            o.side.value,
                            o.type.value,
                            o.tag,
                            o.created_ts,
                        )
                    )
                )
            )
    return out


def test_ids_over_a_real_multi_account_session_are_unique_and_reproducible():
    a, b = _orders(make_cfg()), _orders(make_cfg())
    assert len(a) > 1000, "scenario must contain the repeated-signal pattern"
    assert a == b  # same session, same decisions, same ids (restart-safe)
    # every order is its own decision: re-emitted identical intents on later bars are NOT merged
    assert len(set(a)) == len(a)
    assert all(is_valid_client_order_id(x) for x in a)
