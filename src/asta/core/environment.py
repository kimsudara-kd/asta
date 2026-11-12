"""Environment types. There is deliberately NO `LIVE` member (ADR-0002)."""
from __future__ import annotations

from enum import StrEnum


class Environment(StrEnum):
    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    EXCHANGE_DEMO = "EXCHANGE_DEMO"


#: Environments in which an order could ever be submitted in an authorised milestone.
ORDER_CAPABLE_ENVIRONMENTS: frozenset[Environment] = frozenset(
    {Environment.PAPER, Environment.EXCHANGE_DEMO}
)
