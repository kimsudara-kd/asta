"""The durable replay store of the credential-side service (staged, offline)."""

from __future__ import annotations

import os
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "proposals/binance_testnet_signer"))
import replay_store as rs  # type: ignore[import-not-found]  # noqa: E402

NOW = 1_800_000_000


def test_accepts_once_then_refuses_replays_reused_nonces_and_old_sequences(tmp_path):
    s = rs.FileReplayStore(str(tmp_path / "r.db"))
    assert s.durable is True
    assert s.accept("app", 5, "n1", NOW + 30, NOW) == ""
    assert s.accept("app", 5, "n2", NOW + 30, NOW) == "REPLAYED_OR_OUT_OF_ORDER"
    assert s.accept("app", 4, "n3", NOW + 30, NOW) == "REPLAYED_OR_OUT_OF_ORDER"
    assert s.accept("app", 6, "n1", NOW + 30, NOW) == "NONCE_REUSED"
    assert s.accept("app", 6, "n4", NOW + 30, NOW) == ""
    assert (
        s.accept("other", 1, "n1", NOW + 30, NOW) == ""
    )  # a different sender has its own sequence and nonces


def test_a_restart_cannot_re_accept_anything(tmp_path):
    p = str(tmp_path / "r.db")
    a = rs.FileReplayStore(p)
    assert a.accept("app", 10, "n1", NOW + 30, NOW) == ""
    a.close()
    b = rs.FileReplayStore(p)  # "the process restarted"
    assert b.accept("app", 10, "n9", NOW + 30, NOW) == "REPLAYED_OR_OUT_OF_ORDER"
    assert b.accept("app", 11, "n1", NOW + 30, NOW) == "NONCE_REUSED"
    assert b.accept("app", 11, "n2", NOW + 30, NOW) == ""


def test_concurrent_attempts_accept_exactly_one(tmp_path):
    s = rs.FileReplayStore(str(tmp_path / "r.db"))
    results: list[str] = []

    def go(i: int) -> None:
        results.append(s.accept("app", 7, f"n{i}", NOW + 30, NOW))

    threads = [threading.Thread(target=go, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("") == 1 and len(results) == 20


def test_an_unsafe_file_is_refused_and_a_broken_store_never_accepts(tmp_path):
    p = tmp_path / "r.db"
    p.write_bytes(b"")
    os.chmod(p, 0o644)
    with pytest.raises(rs.StoreUnsafe):
        rs.FileReplayStore(str(p))
    os.chmod(p, 0o600)
    s = rs.FileReplayStore(str(p))
    s._db.close()  # the database goes away underneath it
    assert s.accept("app", 1, "n1", NOW + 30, NOW) == "REPLAY_STORE_ERROR"
    q = tmp_path / "corrupt.db"
    q.write_bytes(b"this is not a sqlite database" * 50)
    os.chmod(q, 0o600)
    with pytest.raises(sqlite3.DatabaseError):
        rs.FileReplayStore(str(q))


def test_it_is_a_replay_store_for_the_real_receiver(tmp_path):
    from asta.orders.extwire import Blake2bAuthenticator, Request, RequestReceiver, seal_request

    auth = Blake2bAuthenticator({"k1": bytes(range(32))})
    rx = RequestReceiver(
        auth,
        frozenset({"app"}),
        rs.FileReplayStore(str(tmp_path / "r.db")),
        allowed_key_ids=frozenset({"k1"}),
    )
    wire = seal_request(
        Request("app", "k1", 3, "nn", NOW, NOW + 30, "QUERY", "a" * 32, "BTC-USDT"), auth
    )
    assert rx.open(wire, NOW)[1] == "" and rx.open(wire, NOW)[1] == "REPLAYED_OR_OUT_OF_ORDER"
