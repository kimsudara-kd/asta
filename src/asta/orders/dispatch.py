"""Exactly-once dispatch and crash/timeout recovery (pure logic over the ports).

Protocol (docs/m3b/ORDER-PATH-DESIGN.md s.4):
  1. CLAIM  the decision durably (unique decision_id). A repeat delivery stops here, never resends.
  2. GUARD  pre-trade refusals (kill switch, caps, allowlists, price band, environment).
  3. WRITE-AHEAD  mark DISPATCHED durably BEFORE the request leaves.
  4. SEND   with client order id = decision_id. ACCEPTED/REJECTED are final answers; DUPLICATE_ID
            means "we already sent this": ask by id; anything ambiguous becomes UNKNOWN.
  5. RECOVER  (after a timeout, crash, restart): for UNKNOWN/DISPATCHED ask the exchange BY
            CLIENT ID.
            FOUND -> adopt its state. NOT_FOUND -> re-send the SAME id (the exchange refuses a
            duplicate, so even an in-flight original cannot be doubled) until max attempts or max
            age, then ABANDONED; an abandoned decision is still adopted if it later appears.
A NEW id is never minted by recovery. That, with the exchange's duplicate refusal, is what makes
submission exactly-once.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import StrEnum

from asta.orders.lifecycle import ALLOWED, TERMINAL, UNRESOLVED, Intent, State
from asta.orders.ports import ExchangePort, IntentStore, QueryKind, SendOutcome
from asta.orders.pretrade import DayState, PreTradeLimits, refusals


class Handled(StrEnum):
    SENT = "SENT"  # processed through to a dispatch attempt (see .state)
    REFUSED = "REFUSED"  # failed validation or a pre-trade guard; nothing was sent
    DUPLICATE_DECISION = "DUPLICATE_DECISION"  # this decision was already claimed: NOT sent again


@dataclass(frozen=True)
class HandleResult:
    outcome: Handled
    state: State | None = None
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class Policy:
    max_attempts: int = 3
    max_age_seconds: int = 300
    #: consecutive NOT_FOUND answers (separate recovery rounds) before the SAME id is re-sent. An
    #: exchange may re-accept a client id once its previous order is filled, and a fast fill can be
    #: invisible to a query for a moment: one NOT_FOUND must never trigger a re-send.
    not_found_confirmations: int = 2


@dataclass
class RecoveryReport:
    adopted: int = 0
    resent: int = 0
    abandoned: int = 0
    unavailable: int = 0
    waiting: int = 0  # NOT_FOUND seen, more confirmations needed before any re-send
    anomalies: list[str] = field(default_factory=list)


class Dispatcher:
    def __init__(
        self,
        exchange: ExchangePort,
        store: IntentStore,
        limits: PreTradeLimits | None,
        day_state: Callable[[], DayState],
        reference_price: Callable[[str], Decimal | None],
        now: Callable[[], int],
        policy: Policy = Policy(),  # noqa: B008  (frozen dataclass: safe shared default)
    ) -> None:
        self.exchange, self.store, self.limits = exchange, store, limits
        self.day_state, self.reference_price, self.now, self.policy = (
            day_state,
            reference_price,
            now,
            policy,
        )

    # ------------------------------------------------------------------ intake
    def handle(self, intent: Intent) -> HandleResult:
        try:
            intent.validate()
        except ValueError as exc:
            return HandleResult(Handled.REFUSED, None, (f"INVALID:{exc}",))
        if not self.store.insert_if_absent(intent):  # 1. CLAIM
            return HandleResult(Handled.DUPLICATE_DECISION, None, ())
        why = refusals(  # 2. GUARD
            intent, self.limits, self.day_state(), self.reference_price(intent.symbol)
        )
        if why:
            self.store.save(intent.moved(State.REJECTED, "refused locally: " + ",".join(why)))
            return HandleResult(Handled.REFUSED, State.REJECTED, why)
        done = self._dispatch(intent)
        return HandleResult(Handled.SENT, done.state, ())

    # ------------------------------------------------------------------ dispatch (steps 3-4)
    def _dispatch(self, rec: Intent) -> Intent:
        day = self.day_state()
        if day.kill_switch:
            return self._save(rec.moved(State.ABANDONED, "kill switch before dispatch"))
        if rec.attempts >= self.policy.max_attempts:
            return self._save(rec.moved(State.ABANDONED, "max attempts"))
        rec = self._save(rec.moved(State.DISPATCHED, attempts=rec.attempts + 1))  # 3. WRITE-AHEAD
        out = self.exchange.send(rec)  # 4. SEND
        if out == SendOutcome.ACCEPTED:
            return self._save(rec.moved(State.ACCEPTED))
        if out == SendOutcome.REJECTED:
            return self._save(rec.moved(State.REJECTED, "exchange refused"))
        if out == SendOutcome.DUPLICATE_ID:
            return self._adopt_by_query(rec) or self._save(rec.moved(State.UNKNOWN, "duplicate id"))
        return self._save(rec.moved(State.UNKNOWN, "ambiguous send"))

    # ------------------------------------------------------------------ recovery (step 5)
    def recover(self) -> RecoveryReport:
        rep = RecoveryReport()
        for rec in sorted(self.store.by_state(UNRESOLVED), key=lambda r: r.decision_id):
            if rec.state == State.DISPATCHED:  # crash window: the request may be on the wire
                rec = self._save(rec.moved(State.UNKNOWN, "recovered after DISPATCHED"))
            if rec.state == State.PENDING:  # never marked dispatched: it was never sent
                self._retry(rec, rep)
                continue
            q = self.exchange.query(rec.decision_id)
            if q.kind == QueryKind.UNAVAILABLE:
                rep.unavailable += 1
            elif q.kind == QueryKind.FOUND and q.view is not None:
                self._adopt(rec, q.view.state, rep)
            else:  # NOT_FOUND now: count it; re-send the SAME id only after enough confirmations
                rec = self._save(replace(rec, misses=rec.misses + 1))
                stale = self.now() - rec.created_ts > self.policy.max_age_seconds
                killed = self.day_state().kill_switch
                if rec.misses >= self.policy.not_found_confirmations or stale or killed:
                    self._retry(rec, rep)
                else:
                    rep.waiting += 1
        for rec in sorted(
            self.store.by_state(frozenset({State.ABANDONED})), key=lambda r: r.decision_id
        ):
            q = self.exchange.query(rec.decision_id)
            if (
                q.kind == QueryKind.FOUND and q.view is not None
            ):  # it did reach the exchange after all
                self._adopt(rec, q.view.state, rep)
        resting = frozenset({State.ACCEPTED, State.PARTIALLY_FILLED})
        for rec in sorted(self.store.by_state(resting), key=lambda r: r.decision_id):
            q = self.exchange.query(rec.decision_id)
            if q.kind == QueryKind.FOUND and q.view is not None and q.view.state != rec.state:
                self._adopt(rec, q.view.state, rep)
        return rep

    def _retry(self, rec: Intent, rep: RecoveryReport) -> None:
        too_old = self.now() - rec.created_ts > self.policy.max_age_seconds
        if too_old or self.day_state().kill_switch or rec.attempts >= self.policy.max_attempts:
            reason = "stale" if too_old else "kill switch / max attempts"
            self._save(rec.moved(State.ABANDONED, f"recovery gave up: {reason}"))
            rep.abandoned += 1
            return
        self._dispatch(rec)
        rep.resent += 1

    def _adopt(self, rec: Intent, seen: State, rep: RecoveryReport) -> None:
        if seen == rec.state:
            return
        if seen not in ALLOWED[rec.state]:
            rep.anomalies.append(
                f"{rec.decision_id}: {rec.state.value} -> {seen.value} not allowed"
            )
            return
        self._save(rec.moved(seen, "adopted from exchange"))
        rep.adopted += 1

    def _adopt_by_query(self, rec: Intent) -> Intent | None:
        q = self.exchange.query(rec.decision_id)
        if q.kind == QueryKind.FOUND and q.view is not None and q.view.state in ALLOWED[rec.state]:
            return self._save(rec.moved(q.view.state, "adopted from exchange"))
        return None

    def _save(self, rec: Intent) -> Intent:
        self.store.save(rec)
        return rec


__all__ = ["Dispatcher", "HandleResult", "Handled", "Policy", "RecoveryReport", "TERMINAL"]
