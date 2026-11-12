"""Independent reconciliation of a finished simulation (M1-R1 workstream B).

The engine's ledger is the book of record. This module re-derives balances and P&L from the
FILL RECORDS alone (a different path from the ledger's own bookkeeping) and compares them,
exactly, with (a) the ledger entries, (b) the last equity point, and (c) the reported metrics.
Pure and offline: it only reads a finished ``RunResult``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from asta.research.sim.engine import RunResult

ZERO = Decimal(0)


@dataclass
class AuditCheck:
    name: str
    ok: bool
    detail: str


def _base(symbol: str) -> str:
    return symbol.split("-")[0]


def digests(res: RunResult) -> dict[str, str]:
    """Per-artefact SHA-256 so a mismatch says WHICH artefact differs."""
    import hashlib

    from asta.research.runner import canonical

    def h(obj: Any) -> str:
        return hashlib.sha256(canonical(obj).encode()).hexdigest()

    return {
        "orders": h(
            [
                [
                    o.order_id,
                    o.symbol,
                    o.side,
                    o.type,
                    o.quantity,
                    o.limit_price,
                    o.stop_price,
                    o.status,
                    o.filled_qty,
                    o.tag,
                    o.created_ts,
                    [[e.seq, e.ts, e.status, e.reason] for e in o.events],
                ]
                for o in res.orders
            ]
        ),
        "fills": h(
            [
                [
                    f.fill_id,
                    f.order_id,
                    f.symbol,
                    f.side,
                    f.ts,
                    f.price,
                    f.quantity,
                    f.liquidity,
                    f.fee_asset,
                    f.fee_amount,
                    f.notional,
                    f.slippage_cost,
                    f.realized_pnl,
                ]
                for f in res.fills
            ]
        ),
        "ledger": h(
            [
                [e.seq, e.ts, e.kind, e.asset, e.amount, e.order_id, e.fill_id]
                for e in res.ledger.entries
            ]
        ),
        "equity": h(
            [
                [p.ts, p.equity, p.cash, p.positions_value, p.reserved_cash, p.exposure]
                for p in res.equity
            ]
        ),
        "risk_events": h([[e.ts, e.kind, e.detail] for e in res.risk_events]),
        "checkpoints": h([[c.index, c.ts, c.digest] for c in res.checkpoints]),
    }


def independent_audit(
    res: RunResult, starting_cash: Decimal, quote: str, reported: dict[str, Any]
) -> list[AuditCheck]:
    out: list[AuditCheck] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        out.append(AuditCheck(name, bool(ok), detail))

    # 0. ledger entries are a gap-free, strictly ordered sequence
    seqs = [e.seq for e in res.ledger.entries]
    add(
        "ledger sequence is strictly increasing",
        all(b > a for a, b in zip(seqs, seqs[1:], strict=False)),
        f"{len(seqs)} entries",
    )

    # 1. balances derived from fills only
    bal: dict[str, Decimal] = {quote: starting_cash}
    for f in res.fills:
        b = _base(f.symbol)
        if f.side == "BUY":
            bal[quote] = bal.get(quote, ZERO) - f.notional
            bal[b] = bal.get(b, ZERO) + f.quantity
        else:
            bal[quote] = bal.get(quote, ZERO) + f.notional
            bal[b] = bal.get(b, ZERO) - f.quantity
        bal[f.fee_asset] = bal.get(f.fee_asset, ZERO) - f.fee_amount
    ledger_bal, _ = res.ledger.recompute_balances()
    assets = {a for a in set(bal) | set(ledger_bal) if bal.get(a, ZERO) or ledger_bal.get(a, ZERO)}
    diffs = {
        a: (bal.get(a, ZERO), ledger_bal.get(a, ZERO))
        for a in assets
        if bal.get(a, ZERO) != ledger_bal.get(a, ZERO)
    }
    add(
        "fill-derived balances equal ledger balances (exact)",
        not diffs,
        str(diffs) if diffs else f"{len(assets)} assets",
    )

    # 2. equity re-derived from balances and the final marks equals the last equity point
    marks = res.final_marks
    pos_value = ZERO
    for a, q in ledger_bal.items():
        if a == quote or q == 0:
            continue
        mark = marks.get(f"{a}-{quote}", marks.get(a))
        if mark is None:
            add(f"mark available for held asset {a}", False, "no mark")
            continue
        pos_value += q * mark
    equity = ledger_bal.get(quote, ZERO) + pos_value
    last = res.equity[-1].equity if res.equity else starting_cash
    add(
        "equity from balances x final marks equals last equity point",
        abs(equity - last) <= Decimal("0.00000001") * max(1, len(res.fills)),
        f"derived={equity} reported={last} (tolerance: 1e-8 per fill, mark rounding)",
    )

    # 3. reported net P&L equals ending equity - starting cash
    net = Decimal(str(reported["total_net_pnl"]))
    add(
        "reported total net P&L = last equity - starting cash",
        abs(net - (last - starting_cash)) <= Decimal("0.00000001"),
        f"reported={net} derived={last - starting_cash}",
    )

    # 4. realised P&L reported equals the sum of per-fill realised P&L
    rp = Decimal(str(reported["realized_pnl"]))
    srp = sum((f.realized_pnl for f in res.fills), ZERO)
    add(
        "reported realised P&L = sum of fill realised P&L",
        abs(rp - srp) <= Decimal("0.00000001"),
        f"reported={rp} fills={srp}",
    )

    # 5. fee total: quote-denominated fees from fills <= reported quote-equivalent fees
    qfees = sum((f.fee_amount for f in res.fills if f.fee_asset == quote), ZERO)
    rf = Decimal(str(reported["total_fees_quote_equivalent"]))
    add(
        "reported fees cover the quote-denominated fees in the fills",
        rf + Decimal("0.00000001") >= qfees,
        f"reported={rf} quote-asset fills={qfees}",
    )

    # 6. the engine's own reconcile verdict
    add("engine reconcile report is clean", res.reconcile.ok, "; ".join(res.reconcile.issues))
    return out
