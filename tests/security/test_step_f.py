"""Step F guards, offline: the service's testnet mode needs a fresh passing probe for the same key; the probe writes it."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

D = Path(__file__).parents[2] / "proposals/binance_testnet_signer"
sys.path.insert(0, str(D))
import keygen as kg  # type: ignore[import-not-found]  # noqa: E402
import probe as pr  # type: ignore[import-not-found]  # noqa: E402
import run_service as rsv  # type: ignore[import-not-found]  # noqa: E402
from test_step_e import Mock, fs, ht  # type: ignore[import-not-found]  # noqa: E402

KEY = bytes(range(32))
NOW = 1_800_000_000.0


def make_testnet_cfg(d: Path, **over: Any) -> dict[str, Any]:
    auth = d / "auth.key"
    auth.write_text(KEY.hex())
    os.chmod(auth, 0o400)
    pem = d / "tn.pem"
    if not pem.exists():
        kg.generate(str(pem))
    api = d / "api.key"
    api.write_text("TESTNET-API-KEY-ID-0123456789")
    os.chmod(api, 0o400)
    cfg = {
        "mode": "testnet", "socket": str(d / "c.sock"), "auth_key_file": str(auth), "key_id": "k1",
        "replay_db": str(d / "replay.db"), "spend_db": str(d / "spend.db"), "enabled_file": str(d / "enabled"),
        "allowed_uids": [os.getuid()], "senders": ["app"], "caps": {"symbols": ["BTC-USDT"]},
        "testnet": {"private_key_file": str(pem), "api_key_file": str(api), "probe_stamp_file": str(d / "stamp.json")},
    }  # fmt: skip
    cfg.update(over)
    return cfg


def fingerprint(d: Path) -> str:
    return repr(fs.FileEd25519Signer(str(d / "tn.pem"))).split("=")[1].rstrip(">")


def test_testnet_mode_needs_its_block_and_a_fresh_passing_probe_for_the_same_key(tmp_path):
    cfg = make_testnet_cfg(tmp_path)
    p = tmp_path / "c.json"
    bare = dict(cfg)
    del bare["testnet"]
    p.write_text(json.dumps(bare))
    with pytest.raises(rsv.ConfigError, match="testnet"):
        rsv.load_config(str(p))
    p.write_text(json.dumps(cfg))
    rsv.load_config(str(p))  # the config itself is fine ...
    with pytest.raises(rsv.ConfigError, match="no passing probe stamp"):
        rsv.build(
            cfg, clock=lambda: NOW
        )  # ... but without a probe stamp the service refuses to start
    stamp = tmp_path / "stamp.json"
    pr.write_stamp(str(stamp), "somebody-elses-key", int(NOW), "SIGNATURE_AND_ORDER_VALID")
    with pytest.raises(rsv.ConfigError, match="not a pass for this key"):
        rsv.build(cfg, clock=lambda: NOW)
    os.chmod(stamp, 0o600)
    pr.write_stamp(
        str(stamp), fingerprint(tmp_path), int(NOW) - 8 * 86400, "SIGNATURE_AND_ORDER_VALID"
    )
    with pytest.raises(rsv.ConfigError, match="stale"):
        rsv.build(cfg, clock=lambda: NOW)
    os.chmod(stamp, 0o600)
    pr.write_stamp(str(stamp), fingerprint(tmp_path), int(NOW), "SIGNATURE_REJECTED")
    with pytest.raises(rsv.ConfigError, match="not a pass"):
        rsv.build(cfg, clock=lambda: NOW)
    os.chmod(stamp, 0o600)
    pr.write_stamp(str(stamp), fingerprint(tmp_path), int(NOW), "SIGNATURE_AND_ORDER_VALID")
    built = rsv.build(
        cfg, clock=lambda: NOW
    )  # a fresh pass for this key: constructs (no network is touched)
    assert built.socket_path == cfg["socket"]


def test_testnet_mode_refuses_a_missing_or_unsafe_key_or_api_key_file(tmp_path):
    cfg = make_testnet_cfg(tmp_path)
    pr.write_stamp(
        str(tmp_path / "stamp.json"), fingerprint(tmp_path), int(NOW), "SIGNATURE_AND_ORDER_VALID"
    )
    os.chmod(cfg["testnet"]["api_key_file"], 0o644)
    with pytest.raises(rsv.ConfigError, match="testnet mode refused"):
        rsv.build(cfg, clock=lambda: NOW)
    os.chmod(cfg["testnet"]["api_key_file"], 0o400)
    os.chmod(cfg["testnet"]["private_key_file"], 0o644)
    with pytest.raises(rsv.ConfigError, match="testnet mode refused"):
        rsv.build(cfg, clock=lambda: NOW)


def test_the_probe_writes_the_stamp_only_on_a_full_pass(tmp_path):
    m = Mock(tmp_path)
    try:
        pem = tmp_path / "p.pem"
        kg.generate(str(pem))
        s = fs.FileEd25519Signer(str(pem))
        m.pub = s.public_key_b64()
        api = tmp_path / "api.key"
        api.write_text("K")
        os.chmod(api, 0o400)
        stamp = tmp_path / "stamp.json"
        cfg = {
            "stage": "probe",
            "private_key_file": str(pem),
            "api_key_file": str(api),
            "probe_stamp_file": str(stamp),
        }
        t = ht.HttpsTransport(m.ctx, 3.0, test_base=m.base)
        m.order_test = (400, {"code": -2015, "msg": "no"})
        assert pr.run(cfg, t)[1] == 1 and not stamp.exists()  # a failed probe leaves no stamp
        m.order_test = (200, {})
        assert pr.run(cfg, t)[1] == 0
        data = json.loads(stamp.read_text())
        assert data["verdict"] == "SIGNATURE_AND_ORDER_VALID" and data["fingerprint"] == repr(
            s
        ).split("=")[1].rstrip(">")
        assert oct(stamp.stat().st_mode & 0o777) == "0o400"
        pr.read_stamp(
            str(stamp), data["fingerprint"], time.time()
        )  # and it satisfies the service's check
    finally:
        m.stop()


# ------------------------------------------------------------------ the drill (place, confirm, cancel, confirm)
from datetime import UTC, datetime  # noqa: E402
from decimal import Decimal as Dec  # noqa: E402

from tests.orders.fakes import MemoryStore  # noqa: E402
from tests.security.test_credential_service import Rig  # noqa: E402

from asta.orders.boundary import GatedExchange, InternalGate, PortKind  # noqa: E402
from asta.orders.dispatch import Dispatcher  # noqa: E402
from asta.orders.lifecycle import State  # noqa: E402
from asta.orders.pretrade import DayState, PreTradeLimits  # noqa: E402
from asta.orders.testnet_session import build_drill_intent, run_drill  # noqa: E402
from asta.orders.testnet_session import main as drill_main  # noqa: E402
from asta.risk import gateway  # noqa: E402
from asta.risk.gateway import ExecutionGateway  # noqa: E402

LIMITS = PreTradeLimits(
    frozenset({"BINANCE_TESTNET"}), frozenset({"BTC-USDT"}), Dec("20"), 5, Dec("40"), Dec("100")
)
WHEN = datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC)


class Switch:
    def __init__(self, rig: Rig) -> None:
        self.rig, self.on = rig, True

    def active(
        self, scope: str
    ) -> bool:  # models the SQL function: false while the kill switch is latched
        return self.on and not self.rig.kill.flag and scope == "BINANCE_TESTNET"


def drill_rig(monkeypatch: Any, constant: bool = True) -> tuple[Rig, Switch, Dispatcher]:
    monkeypatch.setattr(gateway, "TESTNET_EXECUTION_ALLOWED", constant)
    rig = Rig()
    sw = Switch(rig)
    gx = GatedExchange(rig.port(), PortKind.EXTERNAL, InternalGate(rig.kill, ExecutionGateway(sw)))
    d = Dispatcher(
        gx,
        MemoryStore(),
        LIMITS,
        lambda: DayState(0, Dec(0), False),
        lambda s: Dec("50000"),
        lambda: 1_000,
    )
    return rig, sw, d


def intent(label: str = "drill-1"):
    return build_drill_intent(
        "BTC-USDT", Dec("0.0001"), Dec("49800"), label, WHEN
    )  # 4.98 USDT, 0.4% below the reference


def test_as_shipped_the_drill_is_denied_and_nothing_leaves_the_process(monkeypatch):
    rig, _, d = drill_rig(monkeypatch, constant=False)
    rep = run_drill(d, intent())
    assert rep["ok"] is False and rig.channel_calls == 0 and not rig.exchange.orders
    assert "M0_EXECUTION_DISABLED" in json.dumps(rep)


def test_with_every_condition_met_the_drill_places_confirms_cancels_and_confirms(monkeypatch):
    rig, _, d = drill_rig(monkeypatch)
    rep = run_drill(d, intent())
    assert rep["ok"] is True and rep["final_state"] == "CANCELED"
    assert [s["step"] for s in rep["steps"]] == ["place", "confirm", "cancel", "confirm-cancel"]
    assert [s["state"] for s in rep["steps"]] == ["ACCEPTED", "ACCEPTED", "CANCELED", "CANCELED"]
    (order,) = rig.exchange.orders.values()
    assert order["status"] == "CANCELED" and len(rig.exchange.orders) == 1


def test_the_same_drill_twice_is_one_order_and_a_new_label_is_a_new_decision(monkeypatch):
    rig, _, d = drill_rig(monkeypatch)
    assert (
        intent("a").decision_id == intent("a").decision_id
        and intent("a").decision_id != intent("b").decision_id
    )
    assert run_drill(d, intent("a"))["ok"] is True
    again = run_drill(d, intent("a"))
    assert again["steps"][0]["outcome"] == "DUPLICATE_DECISION" and again["ok"] is False
    assert len(rig.exchange.orders) == 1


def test_the_cancel_still_works_after_a_kill_switch_latch_or_after_enablement_is_switched_off(
    monkeypatch,
):
    for how in ("kill", "off"):
        rig, sw, d = drill_rig(monkeypatch)
        i = intent(f"x-{how}")
        assert d.handle(i).state == State.ACCEPTED
        if how == "kill":
            rig.kill.flag = True
        else:
            sw.on = False
        assert sw.active("BINANCE_TESTNET") is False  # the order path is closed for NEW orders
        after = d.cancel(i.decision_id)  # but the resting order can always be cancelled
        assert after is not None and after.value == "CANCELED"
        assert next(iter(rig.exchange.orders.values()))["status"] == "CANCELED"


def test_the_drill_needs_an_explicit_confirmation_flag_and_a_config():
    assert drill_main(["x"]) == 2
    assert drill_main(["x", "--config", "/nonexistent"]) == 2
    assert drill_main(["x", "--config", "/nonexistent", "--yes"]) == 2


def test_the_drill_intent_is_a_resting_limit_buy_well_inside_the_caps():
    i = intent()
    assert (i.side, i.order_type, i.venue, i.symbol) == (
        "BUY",
        "LIMIT",
        "BINANCE_TESTNET",
        "BTC-USDT",
    )
    assert i.quantity is not None and i.limit_price is not None
    assert i.quantity * i.limit_price < Dec("20")
    assert len(i.decision_id) == 32


# ------------------------------------------------------------------ preflight
def test_the_preflight_is_read_only_and_says_not_ready_as_shipped():
    import subprocess

    r = subprocess.run(
        [sys.executable, str(Path(__file__).parents[2] / "scripts/step_f_preflight.py")],
        capture_output=True,
        text=True,
        timeout=120,
    )  # noqa: S603
    assert r.returncode == 1 and "STEP F NOT READY" in r.stdout
    assert "BLOCKED reviewed constant TESTNET_EXECUTION_ALLOWED" in r.stdout  # ships False
    assert "OWNER" in r.stdout  # items only the owner can decide are listed, never auto-passed
    text = (Path(__file__).parents[2] / "scripts/step_f_preflight.py").read_text()
    for bad in (
        "INSERT",
        "UPDATE ",
        "DELETE",
        "enable_testnet_execution(",
        "urllib",
        "socket",
        "subprocess",
        "os.system",
    ):
        assert bad not in text, bad


def test_ready_requires_every_item_and_an_owner_item_alone_never_makes_it_ready(
    monkeypatch, capsys
):
    sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))
    import step_f_preflight as pf  # type: ignore[import-not-found]

    monkeypatch.setattr(pf, "check_db", lambda url: [])
    monkeypatch.setattr(pf, "check_host", lambda: [])
    monkeypatch.setattr(pf, "check_service", lambda c: [])
    monkeypatch.setattr(pf, "check_misc", lambda: [("owner", "OWNER", "needs the owner")])
    monkeypatch.setattr(pf, "check_constant", lambda: ("c", "PASS", ""))
    assert (
        pf.main(["x"]) == 0
    )  # everything a program can check passes; owner items are listed but do not block
    monkeypatch.setattr(pf, "check_host", lambda: [("h", "BLOCKED", "FileVault off")])
    assert pf.main(["x"]) == 1
