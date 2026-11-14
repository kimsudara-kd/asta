"""Reconciliation (pure): our records versus the exchange's, and paper versus demo.

Three jobs, all fail-loud (every finding is a named code; the caller trips the kill switch on the
serious ones, see ``SERIOUS``):
* ``reconcile_orders``  - the set of orders and their states;
* ``reconcile_balances`` - balances the ledger EXPECTS from the fills versus the exchange report;
* ``classify_reset`` - the testnet is wiped about monthly, unannounced: tell a wipe apart
  from corruption, and stop trading until a human re-baselines either way;
* ``compare_paper_demo`` - execution-quality comparison only (price, fill ratio, fees). It makes no
  profit claim and the testnet's liquidity is not representative, so it is labelled as such.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from asta.orders.fills import FillBook
from asta.orders.lifecycle import UNRESOLVED, Intent, State
from asta.orders.ports import ExchangeView

SERIOUS = frozenset({"UNKNOWN_AT_EXCHANGE", "STATE_MISMATCH", "BALANCE_MISMATCH", "ORDER_MISSING"})
_LIVE = frozenset({State.ACCEPTED, State.PARTIALLY_FILLED, State.CANCEL_REQUESTED})


@dataclass(frozen=True)
class Finding:
    code: str
    subject: str
    detail: str

    @property
    def serious(self) -> bool:
        return self.code in SERIOUS


def reconcile_orders(
    local: list[Intent], exchange: dict[str, ExchangeView], our_prefix_ok: set[str] | None = None
) -> list[Finding]:
    """``exchange`` maps client id -> its view for every order the exchange reports (open + recent).

    ``our_prefix_ok`` is the set of client ids that are legitimately ours even if not in ``local``
    (none by default): an id we never created that appears at the exchange is UNKNOWN_AT_EXCHANGE.
    """
    out: list[Finding] = []
    mine = {i.decision_id: i for i in local}
    ok = our_prefix_ok or set()
    for cid in sorted(exchange):
        if cid not in mine and cid not in ok:
            out.append(Finding("UNKNOWN_AT_EXCHANGE", cid, "an order we never created exists"))
    for cid in sorted(mine):
        rec, seen = mine[cid], exchange.get(cid)
        if seen is None:
            if rec.state in _LIVE:
                out.append(
                    Finding("ORDER_MISSING", cid, f"{rec.state.value} locally, absent there")
                )
            continue
        if rec.state in UNRESOLVED or rec.state == State.ABANDONED:
            out.append(
                Finding(
                    "UNRESOLVED_BUT_PRESENT",
                    cid,
                    f"{rec.state.value} yet exchange says {seen.state.value}",
                )
            )
        elif rec.state != seen.state and rec.state != State.CANCEL_REQUESTED:
            out.append(
                Finding(
                    "STATE_MISMATCH", cid, f"local {rec.state.value}, exchange {seen.state.value}"
                )
            )
    return out


def apply_fills(
    balances: dict[str, Decimal], intent: Intent, book: FillBook, base: str, quote: str
) -> dict[str, Decimal]:
    """Balances after the order's trades (a copy). BUY: +base -quote, SELL reverse, fee debited."""
    out = dict(balances)
    sign = Decimal(1) if intent.side == "BUY" else Decimal(-1)
    for f in book.fills.values():
        out[base] = out.get(base, Decimal(0)) + sign * f.quantity
        out[quote] = out.get(quote, Decimal(0)) - sign * f.quantity * f.price
        out[f.fee_asset] = out.get(f.fee_asset, Decimal(0)) - f.fee
    return out


def reconcile_balances(
    expected: dict[str, Decimal], actual: dict[str, Decimal], tolerance: Decimal = Decimal("0")
) -> list[Finding]:
    out: list[Finding] = []
    for asset in sorted(set(expected) | set(actual)):
        e, a = expected.get(asset, Decimal(0)), actual.get(asset, Decimal(0))
        if abs(e - a) > tolerance:
            out.append(Finding("BALANCE_MISMATCH", asset, f"expected {e}, exchange {a}"))
    return out


@dataclass(frozen=True)
class ResetVerdict:
    reset_suspected: bool
    reason: str


def classify_reset(
    local_live: list[Intent],
    exchange: dict[str, ExchangeView],
    actual_balances: dict[str, Decimal],
    blank_balances: dict[str, Decimal],
) -> ResetVerdict:
    """A wipe looks like: every order we thought was live is gone AND the balances equal a fresh
    account. Anything else (some orders missing, balances not blank) is corruption, not a reset."""
    gone = [i for i in local_live if i.state in _LIVE and i.decision_id not in exchange]
    if not gone:
        return ResetVerdict(False, "no live order is missing")
    still = [i for i in local_live if i.state in _LIVE and i.decision_id in exchange]
    if still:
        return ResetVerdict(
            False, "some orders are present: partial loss is corruption, not a reset"
        )
    keys = set(actual_balances) | set(blank_balances)
    same = all(
        actual_balances.get(k, Decimal(0)) == blank_balances.get(k, Decimal(0)) for k in keys
    )
    if not same:
        return ResetVerdict(False, "orders gone but balances are not the blank-account state")
    return ResetVerdict(True, "all live orders gone and balances equal a fresh account")


DISCLAIMER = (
    "Execution-quality comparison only: SIMULATED vs TESTNET fills. Testnet liquidity is not "
    "representative and no figure here is a profit or a forecast."
)


@dataclass(frozen=True)
class FillStat:
    quantity: Decimal
    avg_price: Decimal | None
    fees_quote: Decimal  # fees converted to quote by the caller (0 if charged in another asset)


@dataclass(frozen=True)
class DecisionComparison:
    decision_id: str
    side: str
    fill_ratio_paper: Decimal
    fill_ratio_demo: Decimal
    price_diff_bps: (
        Decimal | None
    )  # (demo - paper) / paper, signed in the order's disfavour: + = worse
    fee_diff_quote: Decimal


@dataclass(frozen=True)
class Comparison:
    rows: list[DecisionComparison]
    only_paper: list[str]
    only_demo: list[str]
    disclaimer: str = DISCLAIMER


def compare_paper_demo(
    intents: dict[str, Intent], paper: dict[str, FillStat], demo: dict[str, FillStat]
) -> Comparison:
    rows: list[DecisionComparison] = []
    for did in sorted(set(paper) & set(demo)):
        it, p, d = intents.get(did), paper[did], demo[did]
        side = it.side if it else "?"
        want = (it.quantity if it and it.quantity else max(p.quantity, d.quantity)) or Decimal(1)
        bps: Decimal | None = None
        if p.avg_price and d.avg_price:
            raw = (d.avg_price - p.avg_price) / p.avg_price * Decimal(10_000)
            bps = raw if side == "BUY" else -raw  # positive = demo fill was worse for us
        rows.append(
            DecisionComparison(
                did, side, p.quantity / want, d.quantity / want, bps, d.fees_quote - p.fees_quote
            )
        )
    return Comparison(rows, sorted(set(paper) - set(demo)), sorted(set(demo) - set(paper)))
