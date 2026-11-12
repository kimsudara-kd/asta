"""Bounded single-process worker for offline backtests (ADR-0008).

One daemon thread, one job at a time, a bounded queue (callers get HTTP 429 when full), progress
and status persisted in PostgreSQL, cooperative cancellation. No Redis, no subprocesses, no network:
the job loads LOCAL content-addressed datasets and runs the pure engine.
"""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import threading
import uuid
from functools import lru_cache

from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from asta.core.timeutil import utcnow
from asta.db import research_store as rs
from asta.db.research_models import BacktestRun
from asta.research.data.store import DatasetFileStore
from asta.research.runner import BacktestConfig, RunRefused, run_backtest
from asta.research.sim.engine import RunCancelled

log = logging.getLogger("asta.research_jobs")


@lru_cache(maxsize=1)
def current_git_commit() -> str:
    env = os.environ.get("ASTA_GIT_COMMIT")
    if env:
        return env[:64]
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv, no shell, no external input
            ["git", "rev-parse", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return out.stdout.strip()[:64] or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


class QueueFull(Exception):
    pass


class ResearchWorker:
    def __init__(
        self, sessions: sessionmaker[Session], store: DatasetFileStore, queue_size: int = 4
    ) -> None:
        self._sessions = sessions
        self.store = store
        self._q: queue.Queue[tuple[uuid.UUID, BacktestConfig] | None] = queue.Queue(
            maxsize=queue_size
        )
        self._cancel: set[uuid.UUID] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.current: uuid.UUID | None = None

    # ---- lifecycle ----
    def start(self) -> None:
        with self._sessions() as db:
            n = rs.recover_interrupted(db)
            if n:
                log.warning("marked %d interrupted backtest run(s) as FAILED", n)
        self._thread = threading.Thread(target=self._loop, name="research-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._q.put_nowait(None)
        except queue.Full:
            pass
        if self._thread:
            self._thread.join(timeout=10)

    # ---- API ----
    def submit(self, run_id: uuid.UUID, config: BacktestConfig) -> None:
        try:
            self._q.put_nowait((run_id, config))
        except queue.Full:
            raise QueueFull("too many queued backtests; try again shortly") from None

    def request_cancel(self, run_id: uuid.UUID) -> None:
        with self._lock:
            self._cancel.add(run_id)

    def _cancelled(self, run_id: uuid.UUID) -> bool:
        with self._lock:
            return run_id in self._cancel

    # ---- worker thread ----
    def _loop(self) -> None:
        while not self._stop.is_set():
            item = self._q.get()
            if item is None:
                return
            run_id, config = item
            try:
                self._process(run_id, config)
            except Exception:  # noqa: BLE001 - the worker must survive any single job
                log.exception("unexpected worker failure for %s", run_id)
            finally:
                with self._lock:
                    self._cancel.discard(run_id)
                self.current = None

    def _process(self, run_id: uuid.UUID, config: BacktestConfig) -> None:
        with self._sessions() as db:
            row = db.get(BacktestRun, run_id)
            if row is None or row.status != "QUEUED":
                return  # cancelled while queued (or removed): nothing to do
            rs.set_running(db, run_id)
            self.current = run_id
            last_pct = -5

            def progress(k: int, n: int) -> None:
                nonlocal last_pct
                pct = int(100 * k / max(n, 1))
                if pct - last_pct >= 5:
                    last_pct = pct
                    with self._sessions() as pdb:
                        rs.set_progress(pdb, run_id, pct)

            try:
                datasets = {s: self.store.load(c) for s, c in config.dataset_checksums.items()}
                out = run_backtest(
                    config,
                    datasets,
                    git_commit=current_git_commit(),
                    should_cancel=lambda: self._cancelled(run_id),
                    on_progress=progress,
                )
                rs.save_result(db, run_id, out)
            except RunCancelled:
                db.rollback()
                rs.finish_failed(db, run_id, "CANCELLED", "cancelled by user")
            except RunRefused as exc:
                db.rollback()
                rs.finish_failed(db, run_id, "FAILED", f"refused: {exc}")
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                log.exception("backtest %s failed", run_id)
                rs.finish_failed(db, run_id, "FAILED", f"{type(exc).__name__}: {exc}"[:500])


def cancel_queued(db: Session, run_id: uuid.UUID) -> bool:
    """Atomically cancel a run that is still QUEUED. False if it already started or finished."""
    res = db.execute(
        update(BacktestRun)
        .where(BacktestRun.id == run_id, BacktestRun.status == "QUEUED")
        .values(status="CANCELLED", error="cancelled before start", finished_at=utcnow())
    )
    db.commit()
    return bool(getattr(res, "rowcount", 0))
