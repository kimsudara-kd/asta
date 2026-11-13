"""ASTA paper-trading service: composition root for the continuous, supervised simulation.

    python -m asta.paper_service            (binds nothing; talks only to the local database and to
                                             the egress-checked PUBLIC market-data adapters)

Runs independently of the API/browser: public feeds -> durable observations -> STEP jobs -> worker.
There is no credential, signer, private endpoint or order path anywhere in this process; paper
orders are rows produced by the offline simulator (`asta.paper`, which must not import adapters).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import logging
import os
import signal
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from asta.core.events import CandleEvent, MarketEvent, Venue
from asta.core.settings import load_settings
from asta.core.timeutil import ClockMonitor, utcnow
from asta.db.models import MarketRulesVersion
from asta.db.paper_models import MarketFeedConnection, PaperSession
from asta.db.session import make_engine, make_session_factory
from asta.exchanges.binance.adapter import BinancePublicAdapter
from asta.exchanges.common.base import ALLOWLIST_SYMBOLS, BINANCE_ALLOWLIST
from asta.exchanges.common.ws import Reconnected
from asta.exchanges.okx.adapter import OkxPublicAdapter
from asta.paper import jobs, ops
from asta.paper.collector import Collector, set_feed_state
from asta.paper.scheduler import advance_initializing, schedule_steps
from asta.paper.worker import StepWorker
from asta.research.data.models import Bar

log = logging.getLogger("asta.paper_service")

FEED_SILENCE_S = 30  # no event for this long => RECONNECTING + incident
OKX_POLL_S = 15
RECONCILE_EVERY = timedelta(minutes=15)


def rest_interval(venue: str, seconds: int) -> str:
    """Venue-native bar name (OKX uses upper-case H)."""
    name = {60: "1m", 300: "5m", 900: "15m", 3600: "1h"}[seconds]
    return name.replace("h", "H") if venue == "OKX" else name


def native_symbol(venue: str, symbol: str) -> str:
    return BINANCE_ALLOWLIST.native(symbol) if venue == "BINANCE" else symbol


def to_bars(events: Sequence[CandleEvent]) -> list[Bar]:
    return sorted(
        (Bar(e.open_time, e.open, e.high, e.low, e.close, e.volume) for e in events if e.closed),
        key=lambda b: b.open_time,
    )


class PaperService:
    def __init__(
        self,
        factory: sessionmaker[Session],
        binance: Any | None = None,
        okx: Any | None = None,
        clock: Callable[[], datetime] = utcnow,
        worker_id: str | None = None,
    ) -> None:
        self.factory = factory
        self.clock = clock
        self.adapters: dict[str, Any] = {}
        if binance is not None:
            self.adapters["BINANCE"] = binance
        if okx is not None:
            self.adapters["OKX"] = okx
        self.connection_id = f"svc-{uuid.uuid4().hex[:10]}"
        self.collector = Collector(clock, self.connection_id)
        self.worker = StepWorker(factory, worker_id or f"worker-{os.getpid()}", clock=clock)
        self.clockmon = ClockMonitor(timedelta(seconds=5))
        self.queue: asyncio.Queue[tuple[str, MarketEvent]] = asyncio.Queue(maxsize=20000)
        self.db_pool = concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="asta-db")
        self.last_event: dict[str, datetime] = {}
        self.reconnects: dict[str, int] = {}
        self.dropped = 0
        self.stop = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._worker_thread: threading.Thread | None = None
        self.enabled: dict[str, str] = {}  # venue -> "" (ok) or the reason it is disabled

    # ------------------------------------------------------------------ startup verification
    async def verify_venue(self, venue: str) -> None:
        """Prove public REST works and store the current trading rules. Failure DISABLES the venue
        with an explicit reason; nothing is bypassed."""
        adapter = self.adapters[venue]
        try:
            t = await adapter.server_time()
            rules = await adapter.symbol_rules(list(ALLOWLIST_SYMBOLS))
            if {r.symbol for r in rules} != set(ALLOWLIST_SYMBOLS) or not all(
                r.trading for r in rules
            ):
                raise RuntimeError("public instrument rules incomplete or not trading")
            await self._db(self._store_rules, venue, rules, t)
            self.enabled[venue] = ""
            await self._db(ops.resolve_incident, f"venue-disabled:{venue}", "service")
            await self._db(
                set_feed_state,
                venue,
                "RECONNECTING",
                "LIVE",
                "verifying stream",
                self.connection_id,
            )
        except Exception as exc:  # noqa: BLE001 - recorded as the disable reason
            reason = f"{type(exc).__name__}: {exc}"[:300]
            self.enabled[venue] = reason
            await self._db(
                lambda db: ops.open_incident(
                    db,
                    "VENUE_DISABLED",
                    ops.SEV_WARN,
                    f"{venue} feed suspended (verification failed, entries isolated): {reason}",
                    f"venue-disabled:{venue}",
                    None,
                    venue,
                )
            )
            await self._db(
                set_feed_state, venue, "DISABLED", "DISABLED", reason, self.connection_id
            )
            log.warning("venue %s DISABLED: %s", venue, reason)

    def _store_rules(self, venue: str, rules: Sequence[Any], server_time: datetime) -> None:
        with self.factory() as db:
            for r in rules:
                last = db.execute(
                    select(MarketRulesVersion)
                    .where(MarketRulesVersion.venue == venue, MarketRulesVersion.symbol == r.symbol)
                    .order_by(MarketRulesVersion.retrieved_at.desc())
                    .limit(1)
                ).scalar_one_or_none()
                same = (
                    last is not None
                    and (last.tick_size, last.step_size, last.min_qty, last.min_notional)
                    == (r.tick_size, r.step_size, r.min_qty, r.min_notional)
                    and last.retrieved_at > self.clock() - timedelta(days=1)
                )
                if not same:
                    db.add(
                        MarketRulesVersion(
                            venue=venue,
                            symbol=r.symbol,
                            tick_size=r.tick_size,
                            step_size=r.step_size,
                            min_qty=r.min_qty,
                            min_notional=r.min_notional,
                            trading=r.trading,
                            source="LIVE",
                            retrieved_at=r.retrieved_at,
                        )
                    )
            db.commit()

    # ------------------------------------------------------------------ db helper
    async def _db(self, fn: Callable[..., Any], *args: Any) -> Any:
        """Run a synchronous DB function on the single DB thread with its own session."""
        assert self._loop is not None

        def run() -> Any:
            with self.factory() as db:
                out = fn(db, *args) if _wants_session(fn) else fn(*args)
                db.commit()
                return out

        return await self._loop.run_in_executor(self.db_pool, run)

    # ------------------------------------------------------------------ feeds
    async def _stream(self, venue: str) -> None:
        adapter = self.adapters[venue]
        while not self.stop.is_set():
            try:
                async for item in adapter.stream(list(ALLOWLIST_SYMBOLS)):
                    if isinstance(item, Reconnected):
                        if item.attempt > 0:
                            self.reconnects[venue] = self.reconnects.get(venue, 0) + 1
                        continue
                    self.last_event[venue] = item.ingested_ts
                    try:
                        self.queue.put_nowait((venue, item))
                    except asyncio.QueueFull:
                        if isinstance(item, CandleEvent) and item.closed:
                            await self.queue.put((venue, item))  # never drop a closed candle
                        else:
                            self.dropped += 1
                if self.stop.is_set():
                    return
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("%s stream error: %s", venue, exc)
                await asyncio.sleep(10)

    async def _okx_candle_poller(self) -> None:
        """OKX 1m candles come from the PUBLIC REST endpoint (the public WS rejects the candle
        channel; the business WS endpoint is deliberately not allow-listed). Only candles the
        exchange marks confirmed (closed) are used."""
        adapter = self.adapters["OKX"]
        while not self.stop.is_set():
            now = self.clock()
            wait = OKX_POLL_S - (now.second % OKX_POLL_S) + 1
            await asyncio.sleep(wait)
            for sym in ALLOWLIST_SYMBOLS:
                try:
                    for c in await adapter.recent_candles(sym, "1m", 5):
                        if c.closed:
                            await self.queue.put(("OKX", c))
                    self.last_event["OKX"] = max(self.last_event.get("OKX", now), self.clock())
                except Exception as exc:  # noqa: BLE001
                    log.warning("OKX candle poll %s failed: %s", sym, exc)
                await asyncio.sleep(0.3)  # rate-limit friendly: 3 calls per 15 s

    def _start_venue_tasks(self, venue: str) -> None:
        self._tasks.append(asyncio.create_task(self._stream(venue)))
        if venue == "OKX":
            self._tasks.append(asyncio.create_task(self._okx_candle_poller()))

    async def _reverify_loop(self) -> None:
        """A venue that failed verification (e.g. an upstream network block) is retried every 60 s
        with the SAME public checks; it only starts streaming once they pass. Nothing is bypassed."""
        while not self.stop.is_set():
            await asyncio.sleep(60)
            for venue in list(self.adapters):
                if not self.enabled.get(venue):
                    continue  # healthy (empty reason)
                try:
                    await self.verify_venue(venue)
                except Exception as exc:  # noqa: BLE001 - DB/network hiccup: retry next cycle
                    log.warning("re-verify %s failed: %s", venue, exc)
                    continue
                if not self.enabled.get(venue):
                    log.info("venue %s verified again: starting its feed", venue)
                    self._start_venue_tasks(venue)

    async def _writer(self) -> None:
        while not self.stop.is_set() or not self.queue.empty():
            batch: list[tuple[str, MarketEvent]] = []
            try:
                batch.append(await asyncio.wait_for(self.queue.get(), 1.0))
            except TimeoutError:
                continue
            while len(batch) < 500 and not self.queue.empty():
                batch.append(self.queue.get_nowait())
            try:
                await self._db(self._write_batch, batch)
            except Exception as exc:  # noqa: BLE001 - DB outage: events are re-fetched by backfill
                log.error("write batch failed (%s); gap backfill will repair", exc)

    def _write_batch(self, db: Session, batch: list[tuple[str, MarketEvent]]) -> None:
        for venue, ev in batch:
            self.collector.on_event(db, ev, native_symbol(venue, ev.symbol))

    # ------------------------------------------------------------------ supervision loops
    async def _monitor(self) -> None:
        while not self.stop.is_set():
            await asyncio.sleep(5)
            now = self.clock()
            for venue, why in self.enabled.items():
                if why:
                    continue
                last = self.last_event.get(venue)
                silent = last is None or (now - last).total_seconds() > FEED_SILENCE_S
                key = f"feed:{venue}"
                try:
                    await self._db(
                        self._set_state,
                        venue,
                        "RECONNECTING" if silent else "CONNECTED",
                        key,
                        silent,
                    )
                except Exception as exc:  # noqa: BLE001 - a DB hiccup must not kill supervision
                    log.warning("feed monitor %s failed: %s", venue, exc)

    def _set_state(self, db: Session, venue: str, state: str, key: str, silent: bool) -> None:
        set_feed_state(
            db,
            venue,
            state,
            "LIVE",
            "no events" if silent else "",
            self.connection_id,
            self.reconnects.get(venue, 0),
            self._offset_ms(venue),
        )
        if silent:
            ops.open_incident(
                db,
                ops.FEED_DISCONNECTED,
                ops.SEV_WARN,
                f"{venue}: no public market data for {FEED_SILENCE_S}s",
                key,
                None,
                venue,
            )
        else:
            f = db.get(MarketFeedConnection, venue)
            if f is not None and f.last_event_at is None:
                f.last_event_at = self.clock()
            ops.resolve_incident(db, key)

    def _offset_ms(self, venue: str) -> int | None:
        r = self.clockmon.reading(venue)
        return None if r is None else int(r.offset.total_seconds() * 1000)

    async def _clock_loop(self) -> None:
        while not self.stop.is_set():
            for venue, why in self.enabled.items():
                if why:
                    continue
                try:
                    before = self.clock()
                    vt = await self.adapters[venue].server_time()
                    after = self.clock()
                    self.clockmon.record(venue, before, vt, after)
                    ok = self.clockmon.is_drift_ok(venue)
                    await self._db(self._clock_incident, venue, ok)
                except Exception as exc:  # noqa: BLE001
                    log.warning("clock check %s failed: %s", venue, exc)
            await asyncio.sleep(60)

    def _clock_incident(self, db: Session, venue: str, ok: bool) -> None:
        key = f"clock:{venue}"
        if ok:
            ops.resolve_incident(db, key)
        else:
            ops.open_incident(
                db,
                ops.CLOCK_DRIFT,
                ops.SEV_WARN,
                f"{venue}: local clock drift exceeds the safe limit",
                key,
                None,
                venue,
            )

    async def _backfill_loop(self) -> None:
        while not self.stop.is_set():
            await asyncio.sleep(30)
            for venue, why in self.enabled.items():
                if why:
                    continue
                try:
                    gaps = await self._db(lambda db, v=venue: self.collector.open_gaps(db, v)[:3])
                except Exception as exc:  # noqa: BLE001 - DB outage: try again next cycle
                    log.warning("gap listing failed (%s)", exc)
                    continue
                for g in gaps:
                    try:
                        need = min(g.missing + 5, 1000 if venue == "BINANCE" else 300)
                        candles = await self.adapters[venue].recent_candles(g.symbol, "1m", need)
                        st = await self._db(
                            self._apply_backfill, g.id, candles, native_symbol(venue, g.symbol)
                        )
                        log.info("gap %s %s %s -> %s", venue, g.symbol, g.gap_start, st)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("backfill %s %s failed: %s", venue, g.symbol, exc)
                    await asyncio.sleep(1)

    def _apply_backfill(
        self, db: Session, gap_id: int, candles: Sequence[CandleEvent], native: str
    ) -> str:
        from asta.db.paper_models import MarketDataGap

        gap = db.get(MarketDataGap, gap_id)
        assert gap is not None
        st = self.collector.apply_backfill(db, gap, candles, native)
        if st == "UNRECOVERABLE":
            ops.open_incident(
                db,
                ops.DATA_GAP,
                ops.SEV_WARN,
                f"{gap.venue} {gap.symbol}: {gap.missing} candles unrecoverable",
                f"gap:{gap.id}",
                None,
                gap.venue,
                gap.symbol,
            )
        return st

    # ------------------------------------------------------------------ session scheduling
    def history_provider(self, venue: str, symbol: str, tf_seconds: int, limit: int) -> list[Bar]:
        """Called from the DB thread: blocks on the event loop for one REST history request."""
        assert self._loop is not None
        ad = self.adapters[venue]
        interval = rest_interval(venue, tf_seconds)
        coro = (
            ad.candles_paged(symbol, interval, limit)
            if venue == "OKX"
            else ad.recent_candles(symbol, interval, limit)
        )
        return to_bars(asyncio.run_coroutine_threadsafe(coro, self._loop).result(90))

    def _tick(self, db: Session) -> None:
        now = self.clock()
        schedule_steps(db, now)
        advance_initializing(db, self.history_provider, now)
        # periodic reconciliation jobs and completed-day reports for every active session
        for s in db.execute(
            select(PaperSession).where(PaperSession.state.in_(("RUNNING", "PAUSED", "RISK_HALTED")))
        ).scalars():
            slot = int(now.timestamp() // RECONCILE_EVERY.total_seconds())
            jobs.enqueue(db, f"RECONCILE:{s.id}:{slot}", jobs.KIND_RECONCILE, s.id, {})
            # one report job per COMPLETED UTC day on or after the session's first day
            d = (s.started_at or now).date()
            while d < now.date():
                jobs.enqueue(
                    db,
                    f"REPORT:{s.id}:{d.isoformat()}",
                    jobs.KIND_REPORT,
                    s.id,
                    {"day": f"{d.isoformat()}T00:00:00+00:00"},
                )
                d += timedelta(days=1)

    async def _scheduler_loop(self) -> None:
        while not self.stop.is_set():
            try:
                await self._db(self._tick)
            except Exception as exc:  # noqa: BLE001
                log.warning("scheduler tick failed: %s", exc)
            await asyncio.sleep(5)

    def _worker_loop(self) -> None:
        last_beat = 0.0
        while not self.stop.is_set():
            try:
                busy = self.worker.run_once()
                if time.monotonic() - last_beat > 5:
                    self.worker.beat()
                    last_beat = time.monotonic()
            except Exception as exc:  # noqa: BLE001 - DB outage etc.: keep the process alive
                log.warning("worker loop error: %s", exc)
                busy = False
                time.sleep(3)
            if not busy:
                time.sleep(1)
        self.worker.stopping = True

    # ------------------------------------------------------------------ run
    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        for venue in list(self.adapters):
            await self.verify_venue(venue)
        self._tasks: list[asyncio.Task[None]] = []
        tasks = self._tasks
        tasks += [
            asyncio.create_task(self._reverify_loop()),
            asyncio.create_task(self._writer()),
            asyncio.create_task(self._monitor()),
            asyncio.create_task(self._clock_loop()),
            asyncio.create_task(self._backfill_loop()),
            asyncio.create_task(self._scheduler_loop()),
        ]
        for venue, why in self.enabled.items():
            if not why:
                self._start_venue_tasks(venue)
        await self._db(self.collector.restore)
        self._worker_thread = threading.Thread(
            target=self._worker_loop, daemon=True, name="asta-worker"
        )
        self._worker_thread.start()
        log.info("paper service up: venues=%s", {v: (w or "OK") for v, w in self.enabled.items()})
        await self.stop.wait()
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=30)
        for ad in self.adapters.values():
            with contextlib.suppress(Exception):
                await ad.aclose()
        self.db_pool.shutdown(wait=True)


def _wants_session(fn: Callable[..., Any]) -> bool:
    code = getattr(fn, "__code__", None)
    return code is not None and "db" in code.co_varnames[: min(code.co_argcount, 2)]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = load_settings()  # refuses to start if exchange credentials are in the environment
    factory = make_session_factory(make_engine(settings.database_url))
    only = {
        v.strip().upper() for v in os.environ.get("ASTA_PAPER_VENUES", "BINANCE,OKX").split(",")
    }
    svc = PaperService(
        factory,
        BinancePublicAdapter(BINANCE_ALLOWLIST) if "BINANCE" in only else None,
        OkxPublicAdapter() if "OKX" in only else None,
    )

    async def runner() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):  # Windows: only Ctrl+C is delivered
                loop.add_signal_handler(sig, svc.stop.set)
        try:
            await svc.run()
        except KeyboardInterrupt:
            svc.stop.set()

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(runner())


if __name__ == "__main__":
    main()


_ = Venue
