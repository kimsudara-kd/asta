"""Venue synchronisation: bring the venue TRADES into our ledger and reconcile orders and balances.

Everything here is read-only towards the venue (trades, order state, account balances) and goes
through the same gate as a cancel: it keeps working when new orders are closed, and it is denied
wholesale while the gateway is DENY. Ingestion is idempotent (the same trade id twice is one trade;
a repeat with different content is a CONFLICT, written to the append-only conflict log, never
absorbed). The reconciliation compares what OUR ledger expects (baseline balances plus our recorded
fills) with what the venue reports; a serious finding or any conflict latches the kill switch
(RECONCILIATION_ANOMALY).
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from asta.orders.boundary import ExecutionDenied, GatedExchange
from asta.orders.fills import Fill, FillBook
from asta.orders.lifecycle import Intent, State
from asta.orders.ports import ExchangeView, QueryKind
from asta.orders.reconcile import Finding, apply_fills, reconcile_balances, reconcile_orders

MAX_PAGES = 20  # 160 trades per order: far beyond a tiny pilot; more is itself an anomaly
LEDGER_STATES = frozenset(
    {
        State.ACCEPTED,
        State.PARTIALLY_FILLED,
        State.FILLED,
        State.CANCEL_REQUESTED,
        State.CANCELED,
        State.EXPIRED,
    }
)


class FillSink(Protocol):
    def record_fill(self, decision_id: str, fill: Fill) -> Any: ...


class KillTrip(Protocol):
    def trip(self, cause: str, actor: str = ..., reason: str = ...) -> None: ...


@dataclass
class OrderSync:
    decision: str
    venue_trades: int = 0
    new: int = 0
    duplicates: int = 0
    conflicts: list[str] = field(default_factory=list)
    status: str = "OK"  # OK | UNAVAILABLE | NOT_FOUND | DENIED | TOO_MANY_TRADES


def fetch_trades(gx: GatedExchange, intent: Intent) -> tuple[QueryKind, list[Fill], bool]:
    """All trades of one order (pages joined); True last when the venue had more after MAX_PAGES."""
    out: list[Fill] = []
    for _ in range(MAX_PAGES):
        kind, page, more = gx.trades(intent.decision_id, intent, len(out))
        if kind is not QueryKind.FOUND:
            return kind, [], False
        out.extend(page)
        if not more:
            return QueryKind.FOUND, out, False
        if not page:  # a venue that says "more" but sends nothing would loop forever
            return QueryKind.UNAVAILABLE, [], False
    return QueryKind.FOUND, out, True


def sync_order(gx: GatedExchange, sink: FillSink, intent: Intent) -> OrderSync:
    rep = OrderSync(intent.decision_id[:8])
    try:
        kind, fills, overflow = fetch_trades(gx, intent)
    except ExecutionDenied as exc:
        rep.status = "DENIED:" + ",".join(exc.reasons)
        return rep
    if kind is QueryKind.NOT_FOUND:
        rep.status = "NOT_FOUND"
        return rep
    if kind is not QueryKind.FOUND:
        rep.status = "UNAVAILABLE"
        return rep
    if overflow:
        rep.status = "TOO_MANY_TRADES"
        rep.conflicts.append("more trades than a pilot order can have")
        return rep
    rep.venue_trades = len(fills)
    for f in fills:
        try:
            outcome = sink.record_fill(intent.decision_id, f)
        except Exception as exc:  # noqa: BLE001 - FillConflict / FillRejected: written to the conflict log by the store
            rep.conflicts.append(f"{f.trade_id}: {type(exc).__name__}: {exc}")
            continue
        if str(getattr(outcome, "value", outcome)) == "DUPLICATE":
            rep.duplicates += 1
        else:
            rep.new += 1
    return rep


def ledger_fills(sessions: sessionmaker[Session], decision_id: str) -> list[Fill]:
    with sessions() as db:
        rows = db.execute(
            text(
                "SELECT trade_id, quantity, price, fee, fee_asset FROM order_fills"
                " WHERE decision_id = :d ORDER BY id"
            ),
            {"d": decision_id},
        ).all()
    return [
        Fill(
            str(r[0]),
            decision_id,
            Decimal(str(r[1])),
            Decimal(str(r[2])),
            Decimal(str(r[3])),
            str(r[4]),
        )
        for r in rows
    ]


def expected_balances(
    baseline: dict[str, Decimal], intents: Iterable[Intent], fills_of: Callable[[str], list[Fill]]
) -> dict[str, Decimal]:
    """Baseline + the effect of OUR recorded fills (not the venue's say-so)."""
    bal = dict(baseline)
    for it in intents:
        book = FillBook(it.decision_id, it.quantity)
        for f in fills_of(it.decision_id):
            book.apply(f)
        base, quote = it.symbol.split("-")
        bal = apply_fills(bal, it, book, base, quote)
    return {a: v for a, v in bal.items() if v != 0}


@dataclass
class Reconciliation:
    orders: list[OrderSync]
    findings: list[Finding]
    tripped: str = ""
    unavailable: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            not self.findings and not self.unavailable and not any(o.conflicts for o in self.orders)
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "tripped": self.tripped,
            "unavailable": self.unavailable,
            "orders": [o.__dict__ for o in self.orders],
            "findings": [
                {"code": f.code, "subject": f.subject, "detail": f.detail, "serious": f.serious}
                for f in self.findings
            ],
        }


def reconcile_session(
    gx: GatedExchange,
    sink: FillSink,
    fills_of: Callable[[str], list[Fill]],
    intents: list[Intent],
    baseline: dict[str, Decimal],
    kill: KillTrip | None = None,
    current: Callable[[str], Intent | None] | None = None,
    known_ids: Iterable[str] | None = None,
    scan_symbols: Iterable[str] | None = None,
) -> Reconciliation:
    """Sync every order's trades, then compare states, open orders and balances with the venue.

    ``known_ids`` are ALL client ids we ever created (any state); ``scan_symbols`` the symbols
    whose open orders are listed (default: the symbols of ``intents``). An order resting at the
    venue under an id outside ``known_ids`` is UNKNOWN_AT_EXCHANGE. Only OPEN orders are listed:
    an order that already finished at the venue is seen through the trades/balances checks."""
    syncs = [sync_order(gx, sink, i) for i in intents]
    if (
        current is not None
    ):  # refresh local states: ingestion may have advanced them (PARTIALLY_FILLED -> FILLED)
        intents = [current(i.decision_id) or i for i in intents]
    rec = Reconciliation(syncs, [])
    views: dict[str, ExchangeView] = {}
    absent: set[str] = set()  # the venue ANSWERED "no such order": only these can be ORDER_MISSING
    for i in intents:
        try:
            q = gx.query(i.decision_id, i)
        except ExecutionDenied:
            rec.unavailable.append(f"{i.decision_id[:8]}: denied")
            continue
        if q.kind is QueryKind.FOUND and q.view is not None:
            views[i.decision_id] = q.view
        elif q.kind is QueryKind.NOT_FOUND:
            absent.add(i.decision_id)
        else:  # could not ask: not evidence of a problem, so never a serious finding
            rec.unavailable.append(f"{i.decision_id[:8]}: venue unavailable")
    rec.findings += reconcile_orders(
        [i for i in intents if i.decision_id in views or i.decision_id in absent], views
    )
    _scan_open_orders(
        gx, rec, intents, set(known_ids) if known_ids is not None else set(), scan_symbols
    )
    try:
        actual_raw = gx.balances(intents[0] if intents else None)
    except ExecutionDenied:
        actual_raw = None
    if actual_raw is None:
        rec.unavailable.append("balances: venue unavailable")
    else:
        actual = {a: free + locked for a, (free, locked) in actual_raw.items()}
        expected = expected_balances(baseline, intents, fills_of)
        rec.findings += reconcile_balances(expected, actual)
    serious = [f for f in rec.findings if f.serious]
    conflicted = [o for o in rec.orders if o.conflicts]
    if kill is not None and (serious or conflicted):
        why = "; ".join(
            [f"{f.code}:{f.subject}" for f in serious]
            + [f"conflict:{o.decision}" for o in conflicted]
        )[:200]
        kill.trip("RECONCILIATION_ANOMALY", "venue-sync", why)
        rec.tripped = "RECONCILIATION_ANOMALY"
    return rec


def _scan_open_orders(
    gx: GatedExchange,
    rec: Reconciliation,
    intents: list[Intent],
    known: set[str],
    scan_symbols: Iterable[str] | None,
) -> None:
    """List the open orders per symbol; add a finding for each one we never created."""
    if not intents:
        return  # no intent to authorise the read with (and nothing of ours to compare)
    known = known | {i.decision_id for i in intents}
    symbols = sorted(set(scan_symbols) if scan_symbols is not None else {i.symbol for i in intents})
    for sym in symbols:
        try:
            kind, ids, more = gx.open_orders(sym, intents[0])
        except ExecutionDenied:
            rec.unavailable.append(f"open orders {sym}: denied")
            continue
        if kind is not QueryKind.FOUND:
            rec.unavailable.append(f"open orders {sym}: venue unavailable")
            continue
        for cid in sorted(ids - known):
            rec.findings.append(
                Finding("UNKNOWN_AT_EXCHANGE", cid[:48], f"open at the venue ({sym}), not ours")
            )
        if more:
            rec.findings.append(
                Finding("OPEN_ORDERS_OVERFLOW", sym, "more open orders than can be checked")
            )


def snapshot_baseline(gx: GatedExchange, intent: Intent) -> dict[str, str] | None:
    """The venue account's balances as {asset: total}, saved BEFORE a session's first order."""
    raw = gx.balances(intent)
    if raw is None:
        return None
    return {a: format(free + locked, "f") for a, (free, locked) in sorted(raw.items())}


def main(argv: list[str]) -> int:  # pragma: no cover - needs the real stack
    if len(argv) != 4 or argv[1] != "--config" or argv[3] not in ("--snapshot", "--reconcile"):
        print(
            "usage: python -m asta.orders.venue_sync --config FILE (--snapshot | --reconcile)",
            file=sys.stderr,
        )
        return 2
    from asta.core.settings import load_settings
    from asta.db.session import make_engine, make_session_factory
    from asta.ipc.unix_socket import UnixSocketChannel
    from asta.orders.boundary import InternalGate, PortKind
    from asta.orders.enablement import testnet_gateway
    from asta.orders.external_port import ExternalChannelPort
    from asta.orders.extwire import Blake2bAuthenticator
    from asta.orders.sql_store import SqlFillStore, SqlIntentStore, SqlKillSwitch
    from asta.orders.testnet_session import build_drill_intent

    cfg = json.loads(Path(argv[2]).read_text())
    key = bytes.fromhex(Path(cfg["auth_key_file"]).read_text().strip())
    sessions = make_session_factory(make_engine(load_settings().database_url))
    kill = SqlKillSwitch(sessions)
    port = ExternalChannelPort(
        UnixSocketChannel(cfg["socket"], 10.0),
        Blake2bAuthenticator({cfg["key_id"]: key}),
        cfg["sender"],
        cfg["key_id"],
        time.time,
    )
    gx = GatedExchange(port, PortKind.EXTERNAL, InternalGate(kill, testnet_gateway(sessions)))
    store = SqlIntentStore(sessions)
    baseline_path = Path(cfg["baseline_file"])
    if argv[3] == "--snapshot":
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        probe_intent = build_drill_intent(
            cfg["symbol"], Decimal("0.0001"), Decimal("1"), "snapshot", now
        )
        snap = snapshot_baseline(gx, probe_intent)
        if snap is None:
            print("balances unavailable: nothing saved", file=sys.stderr)
            return 1
        baseline_path.write_text(json.dumps(snap))
        print(json.dumps({"saved": len(snap)}))
        return 0
    baseline = {a: Decimal(v) for a, v in json.loads(baseline_path.read_text()).items()}
    intents = sorted(store.by_state(LEDGER_STATES), key=lambda i: i.decision_id)
    known = [i.decision_id for i in store.by_state(frozenset(State))]
    symbols = cfg.get("symbols") or [cfg["symbol"]]
    rec = reconcile_session(
        gx,
        SqlFillStore(sessions),
        lambda d: ledger_fills(sessions, d),
        intents,
        baseline,
        kill,
        store.get,
        known,
        symbols,
    )
    print(json.dumps(rec.to_json(), indent=1))
    return 0 if rec.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv))
