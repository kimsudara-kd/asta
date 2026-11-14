"""Fake exchange + in-memory store for the order-path tests (no network, no credential)."""

from __future__ import annotations

import random
from datetime import UTC, datetime
from decimal import Decimal as D
from typing import Any

from asta.core.environment import Environment
from asta.orders.lifecycle import Intent, State
from asta.orders.ports import (
    CancelOutcome,
    CancelResult,
    ExchangeView,
    QueryKind,
    QueryResult,
    SendOutcome,
)
from asta.paper.order_identity import DecisionKey, decision_id

T0 = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)


class Crash(Exception):
    """Injected process death at any point (memory lost; durable store and exchange survive)."""


def make_intent(i: int, session: str = "s" * 64, **kw: Any) -> Intent:
    key = DecisionKey(session, "DEMO:sma_cross", "BTC-USDT", "BUY", "MARKET", f"t{i}", T0)
    base = {
        "decision_id": decision_id(key),
        "environment": Environment.EXCHANGE_DEMO,
        "venue": "BINANCE_TESTNET",
        "symbol": "BTC-USDT",
        "side": "BUY",
        "order_type": "MARKET",
        "quote_notional": D("10"),
        "created_ts": 1_000,
    }
    base.update(kw)
    return Intent(**base)  # type: ignore[arg-type]


class MemoryStore:
    """Durable store stand-in: survives 'crashes' (the Dispatcher is rebuilt, this is kept)."""

    def __init__(self) -> None:
        self.rows: dict[str, Intent] = {}
        self.saves = 0
        self.crash_on_save: int | None = (
            None  # raise Crash at this save() ordinal (after persisting it)
        )

    def insert_if_absent(self, intent: Intent) -> bool:
        if intent.decision_id in self.rows:
            return False
        self.rows[intent.decision_id] = intent
        return True

    def get(self, decision_id: str) -> Intent | None:
        return self.rows.get(decision_id)

    def state_of(self, decision_id: str) -> State:
        return self.rows[decision_id].state

    def attempts_of(self, decision_id: str) -> int:
        return self.rows[decision_id].attempts

    def save(self, intent: Intent) -> None:
        self.rows[intent.decision_id] = intent
        self.saves += 1
        if self.crash_on_save is not None and self.saves == self.crash_on_save:
            self.crash_on_save = None
            raise Crash("after save")

    def by_state(self, states: frozenset[State]) -> list[Intent]:
        return [r for r in self.rows.values() if r.state in states]


class FakeExchange:
    """Unique client ids (a duplicate is refused), lossy transport, eventual-consistency queries."""

    def __init__(
        self,
        rng: random.Random,
        p_drop_request: float = 0.0,
        p_drop_response: float = 0.0,
        p_unavailable: float = 0.0,
        p_crash_after_send: float = 0.0,
        visible_delay: int = 0,
        reuse_after_fill: bool = False,
    ) -> None:
        self.rng = rng
        self.p_drop_request, self.p_drop_response = p_drop_request, p_drop_response
        self.p_unavailable, self.p_crash_after_send = p_unavailable, p_crash_after_send
        self.visible_delay = visible_delay
        self.orders: dict[str, State] = {}
        self.visible_at: dict[str, int] = {}
        self.tick = 0
        self.send_calls = 0
        self.duplicates_refused = 0
        #: models an exchange that re-accepts a client id once the previous order is FILLED
        self.reuse_after_fill = reuse_after_fill
        self.double_orders = 0
        self.cancel_effects = 0
        self.created_at_tick: dict[str, int] = {}

    def healthy(self) -> None:
        self.p_drop_request = self.p_drop_response = self.p_unavailable = 0.0
        self.p_crash_after_send = 0.0
        self.visible_delay = 0

    def advance(self) -> None:
        self.tick += 1
        for cid, st in list(self.orders.items()):
            if st == State.ACCEPTED and self.rng.random() < 0.5:
                self.orders[cid] = State.PARTIALLY_FILLED
            elif st == State.PARTIALLY_FILLED and self.rng.random() < 0.5:
                self.orders[cid] = State.FILLED

    def send(self, intent: Intent) -> SendOutcome:
        self.send_calls += 1
        if self.rng.random() < self.p_drop_request:
            return SendOutcome.UNKNOWN  # never arrived
        if intent.decision_id in self.orders:
            if self.reuse_after_fill and self.orders[intent.decision_id] == State.FILLED:
                self.double_orders += 1  # a SECOND real order under the same id
                return SendOutcome.ACCEPTED
            self.duplicates_refused += 1
            return SendOutcome.DUPLICATE_ID
        self.orders[intent.decision_id] = State.ACCEPTED
        self.created_at_tick[intent.decision_id] = self.tick
        self.visible_at[intent.decision_id] = self.tick + self.visible_delay
        if self.rng.random() < self.p_crash_after_send:
            raise Crash("after send, before the reply was processed")
        if self.rng.random() < self.p_drop_response:
            return SendOutcome.UNKNOWN  # arrived, but we never heard (visibility may lag)
        self.visible_at[intent.decision_id] = (
            self.tick
        )  # an acknowledged order is queryable at once
        return SendOutcome.ACCEPTED

    def cancel(self, client_id: str) -> CancelResult:
        if self.rng.random() < self.p_drop_request:
            return CancelResult(CancelOutcome.UNKNOWN)  # never arrived
        if client_id not in self.orders:
            return CancelResult(CancelOutcome.UNKNOWN_ORDER)
        state = self.orders[client_id]
        if state not in (State.ACCEPTED, State.PARTIALLY_FILLED):
            return CancelResult(CancelOutcome.FINAL, state)  # too late
        self.orders[client_id] = State.CANCELED
        self.cancel_effects += 1
        if self.rng.random() < self.p_drop_response:
            return CancelResult(CancelOutcome.UNKNOWN)  # cancelled, but we never heard
        return CancelResult(CancelOutcome.CANCELED)

    def query(self, client_id: str) -> QueryResult:
        if self.rng.random() < self.p_unavailable:
            return QueryResult(QueryKind.UNAVAILABLE)
        if client_id not in self.orders or self.tick < self.visible_at[client_id]:
            return QueryResult(QueryKind.NOT_FOUND)
        return QueryResult(QueryKind.FOUND, ExchangeView(client_id, self.orders[client_id]))


class FlagKill:
    """Test kill state: ``flag`` is flipped by the test (the real one is durable, see sql_store)."""

    def __init__(self, active: bool = False) -> None:
        self.flag = active

    def active(self) -> bool:
        return self.flag


def gated(ex: Any, kill: Any = None) -> Any:
    from asta.orders.boundary import GatedExchange, InternalGate, PortKind

    return GatedExchange(ex, PortKind.SIMULATED, InternalGate(kill or FlagKill()))
