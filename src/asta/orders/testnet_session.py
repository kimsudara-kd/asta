"""Step F manual drill: place ONE resting testnet order, confirm it, cancel it, confirm the cancel.

    python -m asta.orders.testnet_session --config FILE --confirm-testnet

It goes through the whole real path (dispatcher, gate, gateway, channel, credential service,
testnet) and is INERT unless every other condition holds: the reviewed constant, a runtime
enablement by a listed releaser, an unlatched kill switch and a running credential service. As
shipped the gateway denies it and nothing leaves this process. The order rests away from the
market (the owner supplies price and reference) and is cancelled at once; no fill is expected.
Config keys: socket, auth_key_file, key_id, sender, symbol, quantity, limit_price, reference_price.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from asta.core.environment import Environment
from asta.orders.boundary import ExecutionDenied
from asta.orders.dispatch import Dispatcher
from asta.orders.lifecycle import Intent, State
from asta.paper.order_identity import DecisionKey, decision_id

SESSION_HASH = "5" * 64  # a fixed, obviously-manual session; the label below separates drills
VENUE = "BINANCE_TESTNET"


def build_drill_intent(
    symbol: str, quantity: Decimal, limit_price: Decimal, label: str, now: datetime
) -> Intent:
    key = DecisionKey(
        SESSION_HASH,
        "MANUAL:step-f-drill",
        symbol,
        "BUY",
        "LIMIT",
        label,
        now.replace(microsecond=0),
    )
    return Intent(
        decision_id=decision_id(key),
        environment=Environment.EXCHANGE_DEMO,
        venue=VENUE,
        symbol=symbol,
        side="BUY",
        order_type="LIMIT",
        quantity=quantity,
        limit_price=limit_price,
        created_ts=int(now.timestamp()),
    )


def run_drill(d: Dispatcher, intent: Intent) -> dict[str, Any]:
    """Place, confirm, cancel, confirm. Never raises on a denial: it reports it."""
    rep: dict[str, Any] = {"decision": intent.decision_id[:8], "steps": []}

    def state() -> str:
        rec = d.store.get(intent.decision_id)
        return rec.state.value if rec else "NONE"

    def note() -> str:
        rec = d.store.get(intent.decision_id)
        return rec.note if rec else ""

    try:
        res = d.handle(intent)
        rep["steps"].append(
            {
                "step": "place",
                "outcome": res.outcome.value,
                "state": state(),
                "reasons": list(res.reasons),
                "note": note(),
            }
        )
        if res.state not in (State.ACCEPTED, State.PARTIALLY_FILLED, State.FILLED):
            rep["ok"] = False
            return rep
        d.recover()  # asks the venue by client id: confirms what rests there
        rep["steps"].append({"step": "confirm", "state": state()})
        if state() in ("ACCEPTED", "PARTIALLY_FILLED"):
            after = d.cancel(intent.decision_id)
            rep["steps"].append({"step": "cancel", "state": after.value if after else "NONE"})
            d.recover()
            rep["steps"].append({"step": "confirm-cancel", "state": state()})
        rep["ok"] = state() == "CANCELED"
    except ExecutionDenied as exc:
        rep["steps"].append({"step": "denied", "reasons": list(exc.reasons)})
        rep["ok"] = False
    rep["final_state"] = state()
    return rep


def main(argv: list[str]) -> int:  # pragma: no cover - needs the real stack
    if len(argv) != 4 or argv[1] != "--config" or argv[3] != "--confirm-testnet":
        print(
            "usage: python -m asta.orders.testnet_session --config FILE --confirm-testnet",
            file=sys.stderr,
        )
        return 2
    from asta.core.settings import load_settings
    from asta.db.session import make_engine, make_session_factory
    from asta.ipc.unix_socket import UnixSocketChannel
    from asta.orders.boundary import GatedExchange, InternalGate, PortKind
    from asta.orders.enablement import testnet_gateway
    from asta.orders.external_port import ExternalChannelPort
    from asta.orders.extwire import Blake2bAuthenticator
    from asta.orders.pretrade import DayState, PreTradeLimits
    from asta.orders.sql_store import SqlIntentStore, SqlKillSwitch

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
    sym = cfg["symbol"]
    limits = PreTradeLimits(
        frozenset({VENUE}), frozenset({sym}), Decimal("20"), 5, Decimal("40"), Decimal(100)
    )
    ref = Decimal(str(cfg["reference_price"]))
    d = Dispatcher(
        gx,
        SqlIntentStore(sessions),
        limits,
        lambda: DayState(0, Decimal(0), False),
        lambda s: ref,
        lambda: int(time.time()),
    )
    now = datetime.now(UTC)
    intent = build_drill_intent(
        sym,
        Decimal(str(cfg["quantity"])),
        Decimal(str(cfg["limit_price"])),
        f"drill-{now:%Y%m%d%H%M%S}",
        now,
    )
    report = run_drill(d, intent)
    print(json.dumps(report, indent=1))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv))
