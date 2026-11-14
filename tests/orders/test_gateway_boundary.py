"""ASTA-DECISION-007 workstream A: one mandatory gateway boundary; no path bypasses it."""

from __future__ import annotations

import ast
import random
from decimal import Decimal as D
from pathlib import Path

import pytest

from asta.core import order_gate
from asta.core.order_gate import OrderGateDenied, Surface
from asta.orders.boundary import (
    ExecutionDenied,
    GatedExchange,
    InternalGate,
    PortKind,
)
from asta.orders.dispatch import Dispatcher, Handled
from asta.orders.lifecycle import State
from asta.orders.pretrade import DayState, PreTradeLimits
from tests.orders.fakes import FakeExchange, FlagKill, MemoryStore, make_intent

SRC = Path(__file__).parents[2] / "src" / "asta"
ROOT = SRC.parents[1]
LIM = PreTradeLimits(
    frozenset({"BINANCE_TESTNET"}), frozenset({"BTC-USDT"}), D("50"), 99, D("5000"), D("100")
)
DAY = DayState(0, D(0), False)


def disp(port, kill=None, kind=PortKind.SIMULATED):
    gx = GatedExchange(port, kind, InternalGate(kill or FlagKill()))
    return Dispatcher(gx, MemoryStore(), LIM, lambda: DAY, lambda s: D("100"), lambda: 1_000)


# ------------------------------------------------------------------ the shared contract
def test_external_is_always_denied_and_nothing_changes_that():
    for env in ("", "PAPER", "EXCHANGE_DEMO", "BACKTEST", "LIVE"):
        assert not order_gate.authorize(Surface.EXTERNAL, env).allowed
    with pytest.raises(OrderGateDenied):
        order_gate.require(Surface.EXTERNAL, "EXCHANGE_DEMO")
    assert order_gate.authorize(Surface.SIMULATION).allowed
    assert order_gate.authorize(Surface.DEMO_INTENT, "EXCHANGE_DEMO").allowed
    assert not order_gate.authorize(Surface.DEMO_INTENT, "PAPER").allowed


def test_the_contract_module_cannot_be_configured():
    tree = ast.parse((SRC / "core/order_gate.py").read_text())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    }
    assert not names & {"environ", "getenv", "settings", "load_settings", "open"}
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert imported <= {"__future__", "dataclasses", "enum"}


# ------------------------------------------------------------------ runtime bypass-negative tests
def test_the_dispatcher_refuses_a_raw_port():
    with pytest.raises(TypeError):
        Dispatcher(
            FakeExchange(random.Random(1)),
            MemoryStore(),
            LIM,
            lambda: DAY,
            lambda s: D("100"),
            lambda: 1,
        )  # type: ignore[arg-type]


def test_an_external_port_is_never_called_not_for_send_query_or_cancel():
    ex = FakeExchange(random.Random(1))
    d = disp(ex, kind=PortKind.EXTERNAL)
    r = d.handle(make_intent(1))
    assert r.state == State.REJECTED and ex.send_calls == 0 and not ex.orders
    assert "M0_EXECUTION_DISABLED" in d.store.rows[make_intent(1).decision_id].note
    for call in (lambda: d.exchange.query("x" * 32), lambda: d.exchange.cancel("x" * 32)):
        with pytest.raises(ExecutionDenied):
            call()
    with pytest.raises(ExecutionDenied):
        d.exchange.send(make_intent(2))  # even a direct call on the gated port is refused
    assert ex.send_calls == 0


def test_a_port_with_no_declared_kind_is_refused():
    ex = FakeExchange(random.Random(1))
    d = disp(ex, kind=None)
    assert d.handle(make_intent(1)).state == State.REJECTED and ex.send_calls == 0
    with pytest.raises(ExecutionDenied) as e:
        d.exchange.send(make_intent(1))
    assert "PORT_KIND_UNDECLARED" in e.value.reasons


def test_the_latched_kill_switch_stops_the_gate_for_every_call_path():
    ex, k = FakeExchange(random.Random(1)), FlagKill(True)
    d = disp(ex, k)
    r = d.handle(make_intent(1))
    assert r.outcome == Handled.REFUSED and "KILL_SWITCH" in r.reasons and ex.send_calls == 0
    with pytest.raises(ExecutionDenied) as e:
        d.exchange.send(make_intent(2))  # a caller that skips the dispatcher is stopped too
    assert "KILL_SWITCH_LATCHED" in e.value.reasons and ex.send_calls == 0
    k.flag = False
    assert d.handle(make_intent(3)).state == State.ACCEPTED


class FlipKill:
    """Not latched for the first ``n`` questions, latched afterwards (a halt arriving mid-flight)."""

    def __init__(self, n: int) -> None:
        self.n, self.calls = n, 0

    def active(self) -> bool:
        self.calls += 1
        return self.calls > self.n


def test_a_halt_that_arrives_between_the_check_and_the_write_ahead_blocks_the_advance():
    ex = FakeExchange(random.Random(1))
    d = disp(
        ex, FlipKill(3)
    )  # guard, dispatch check, permit pass; the write-ahead guard sees the latch
    d.handle(make_intent(1))
    assert ex.send_calls == 0 and not ex.orders
    assert d.store.rows[make_intent(1).decision_id].state == State.ABANDONED
    assert "raced" in d.store.rows[make_intent(1).decision_id].note


# ------------------------------------------------------------------ architecture / import graph
def _imports(path: Path) -> set[str]:
    out: set[str] = set()
    for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            out.add(n.module)
            out |= {f"{n.module}.{a.name}" for a in n.names}
    return out


def test_nothing_outside_the_orders_package_imports_it():
    offenders = []
    for p in list(SRC.rglob("*.py")) + list((ROOT / "scripts").rglob("*.py")):
        if "orders" in p.relative_to(ROOT).parts[:3] and p.is_relative_to(SRC / "orders"):
            continue
        if any(i == "asta.orders" or i.startswith("asta.orders.") for i in _imports(p)):
            offenders.append(str(p.relative_to(ROOT)))
    # the SQL adapter lives in orders/ itself; api routes, CLI scripts, workers and the paper/research
    # runtimes have no path to the order boundary in this build
    assert offenders == []


def test_only_the_dispatcher_and_the_gated_port_touch_an_exchange_port():
    allowed = {"dispatch.py", "boundary.py"}
    for p in (SRC / "orders").glob("*.py"):
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                tgt = n.func.value
                owner = tgt.attr if isinstance(tgt, ast.Attribute) else getattr(tgt, "id", "")
                if n.func.attr in {"send", "cancel", "query"} and owner in {
                    "exchange",
                    "_port",
                    "port",
                }:
                    assert p.name in allowed, (p.name, n.lineno)
    boundary = ast.parse((SRC / "orders/boundary.py").read_text())
    assert "_port" in {n.attr for n in ast.walk(boundary) if isinstance(n, ast.Attribute)}


def test_nothing_in_src_or_scripts_builds_the_dispatcher_or_a_gated_port_or_implements_a_port():
    names = {"Dispatcher", "GatedExchange", "ExchangePort", "InternalGate"}
    for p in list(SRC.rglob("*.py")) + list((ROOT / "scripts").rglob("*.py")):
        if p.is_relative_to(SRC / "orders"):
            continue
        used = {n.id for n in ast.walk(ast.parse(p.read_text())) if isinstance(n, ast.Name)}
        assert not used & names, (p.name, used & names)


def test_simulated_orders_are_created_only_by_the_engine_and_only_after_the_gate():
    makers = []
    for p in SRC.rglob("*.py"):
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "SimOrder":
                makers.append((p.relative_to(SRC).as_posix(), n.lineno))
    assert [m[0] for m in makers] == ["research/sim/engine.py"]
    eng = ast.parse((SRC / "research/sim/engine.py").read_text())
    submit = next(
        n for n in ast.walk(eng) if isinstance(n, ast.FunctionDef) and n.name == "_submit"
    )
    gate_line = next(
        n.lineno
        for n in ast.walk(submit)
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "authorize"
    )
    assert gate_line < makers[0][1]


def test_no_api_route_creates_or_submits_an_order():
    from asta.api.app import create_app  # noqa: F401  (import proves the app builds)

    src = (
        (SRC / "api" / "app.py").read_text()
        + (SRC / "api" / "paper.py").read_text()
        + (SRC / "api" / "research.py").read_text()
    )
    tree = ast.parse(src)
    bad = []
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef):
            for d in n.decorator_list:
                if (
                    isinstance(d, ast.Call)
                    and isinstance(d.func, ast.Attribute)
                    and d.func.attr in {"post", "put", "patch", "delete"}
                ):
                    path = (
                        ast.literal_eval(d.args[0])
                        if d.args and isinstance(d.args[0], ast.Constant)
                        else ""
                    )
                    if "order" in str(path).lower():
                        bad.append(path)
    assert bad == []
