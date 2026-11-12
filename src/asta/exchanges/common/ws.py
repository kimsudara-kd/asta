"""Reconnecting WebSocket stream helper: egress-checked, backoff with jitter."""
from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

import websockets

from asta.core.egress import assert_url_allowed


@dataclass(frozen=True)
class Backoff:
    base: float = 1.0
    cap: float = 60.0

    def delay(self, attempt: int, rng: Callable[[], float] = random.random) -> float:
        ceiling = min(self.cap, self.base * (2**attempt))
        return ceiling / 2 + rng() * ceiling / 2  # "equal jitter"; never below ceiling/2


class Reconnected:
    """Sentinel yielded after each (re)connect so consumers can mark a data gap."""

    def __init__(self, attempt: int) -> None:
        self.attempt = attempt


async def ws_messages(
    url: str,
    subscribe: list[str] | None = None,
    text_ping: str | None = None,
    ping_every: float = 20.0,
    backoff: Backoff = Backoff(),
    max_attempts: int | None = None,
) -> AsyncIterator[str | Reconnected]:
    """Yield raw text frames forever, reconnecting with backoff."""
    assert_url_allowed(url)
    attempt = 0
    while max_attempts is None or attempt <= max_attempts:
        try:
            async with websockets.connect(
                url, open_timeout=15, ping_interval=None, max_size=2**22
            ) as ws:
                for msg in subscribe or []:
                    await ws.send(msg)
                yield Reconnected(attempt)
                attempt = 0
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=ping_every)
                    except TimeoutError:
                        if text_ping is not None:
                            await ws.send(text_ping)
                        continue
                    if isinstance(raw, bytes):
                        raw = raw.decode()
                    if raw == "pong":
                        continue
                    yield raw
        except (OSError, websockets.WebSocketException, TimeoutError):
            pass
        attempt += 1
        await asyncio.sleep(backoff.delay(attempt))
