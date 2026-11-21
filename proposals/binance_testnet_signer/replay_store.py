"""STAGED (outside src/): a DURABLE replay store for the credential-side service, backed by one SQLite file.

``ReplayStore.accept`` is atomic: it either records (sender, seq, nonce) and returns "" or returns a refusal reason and changes
nothing. A restart can never re-accept a message (the file is the memory), which is what ``RequestReceiver`` requires of a
durable store. Fail closed: if the file cannot be opened, is not owned by this user, is group/other accessible, or any
operation errors, ``accept`` refuses (it never says "" on a problem).
"""

from __future__ import annotations

import os
import sqlite3
import stat
import threading

MAX_NONCES_PER_SENDER = 50_000


class StoreUnsafe(Exception):
    pass


class FileReplayStore:
    durable = True

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._db = self._open(path)

    @staticmethod
    def _open(path: str) -> sqlite3.Connection:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        os.close(fd)
        st = os.stat(path)
        if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o077:
            raise StoreUnsafe("replay store must be owned by this user with mode 0600")
        db = sqlite3.connect(path, isolation_level=None, timeout=5.0, check_same_thread=False)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute(
            "CREATE TABLE IF NOT EXISTS senders (sender TEXT PRIMARY KEY, last_seq INTEGER NOT NULL)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS nonces (sender TEXT NOT NULL, nonce TEXT NOT NULL, "
            "expires_at INTEGER NOT NULL, PRIMARY KEY (sender, nonce))"
        )
        return db

    def accept(self, sender: str, seq: int, nonce: str, expires_at: int, now: int) -> str:
        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                try:
                    self._db.execute("DELETE FROM nonces WHERE expires_at < ?", (now - 3600,))
                    row = self._db.execute(
                        "SELECT last_seq FROM senders WHERE sender = ?", (sender,)
                    ).fetchone()
                    if row is not None and seq <= row[0]:
                        self._db.execute("ROLLBACK")
                        return "REPLAYED_OR_OUT_OF_ORDER"
                    if self._db.execute(
                        "SELECT 1 FROM nonces WHERE sender = ? AND nonce = ?", (sender, nonce)
                    ).fetchone():
                        self._db.execute("ROLLBACK")
                        return "NONCE_REUSED"
                    n = self._db.execute(
                        "SELECT count(*) FROM nonces WHERE sender = ?", (sender,)
                    ).fetchone()[0]
                    if n >= MAX_NONCES_PER_SENDER:
                        self._db.execute("ROLLBACK")
                        return "NONCE_MEMORY_FULL"
                    self._db.execute(
                        "INSERT INTO senders (sender, last_seq) VALUES (?, ?) "
                        "ON CONFLICT(sender) DO UPDATE SET last_seq = excluded.last_seq",
                        (sender, seq),
                    )
                    self._db.execute(
                        "INSERT INTO nonces (sender, nonce, expires_at) VALUES (?, ?, ?)",
                        (sender, nonce, expires_at),
                    )
                    self._db.execute("COMMIT")
                    return ""
                except BaseException:
                    self._db.execute("ROLLBACK")
                    raise
            except Exception:  # noqa: BLE001 - any storage problem is a refusal, never an acceptance
                return "REPLAY_STORE_ERROR"

    def close(self) -> None:
        self._db.close()


class FileSpendStore:
    """Persistent per-day notional spent (review S-03): a service restart can no longer reset the day cap.

    Same safety rules as the replay store (private file, atomic, fail closed: an unreadable store reports a spend so large
    that every further order is refused).
    """

    def __init__(self, path: str) -> None:
        from decimal import Decimal

        self._zero = Decimal(0)
        self._lock = threading.Lock()
        self._db = FileReplayStore._open(path)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS spend (day TEXT PRIMARY KEY, amount TEXT NOT NULL)"
        )

    def get(self, day: str):  # type: ignore[no-untyped-def]
        from decimal import Decimal

        with self._lock:
            try:
                row = self._db.execute("SELECT amount FROM spend WHERE day = ?", (day,)).fetchone()
                return Decimal(row[0]) if row else self._zero
            except Exception:  # noqa: BLE001
                return Decimal("1e12")  # fail closed: looks like the cap is exhausted

    def add(self, day: str, amount) -> None:  # type: ignore[no-untyped-def]
        from decimal import Decimal

        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute("SELECT amount FROM spend WHERE day = ?", (day,)).fetchone()
                total = (Decimal(row[0]) if row else Decimal(0)) + Decimal(amount)
                self._db.execute(
                    "INSERT INTO spend (day, amount) VALUES (?, ?) ON CONFLICT(day) DO UPDATE SET amount = excluded.amount",
                    (day, format(total, "f")),
                )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def close(self) -> None:
        self._db.close()
