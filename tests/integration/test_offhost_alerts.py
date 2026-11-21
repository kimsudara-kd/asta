"""M3-B-FINAL 5E: off-host alert catalogue, signed status stamp, notifier, dead-man (G-13)."""

from __future__ import annotations

import http.server
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from asta.orders import alerting
from asta.orders.monitor import Alert

KEY = b"0123456789abcdef0123456789abcdef"
ROOT = Path(__file__).parents[2]
DEADMAN = ROOT / "infra/security-host/mac/firewall-vm/asta-deadman.sh"


# ----------------------------------------------------------------- stamp (no database)
def test_stamp_roundtrip_and_every_forgery_fails():
    now = 1_000_000.0
    st = alerting.make_stamp(KEY, [], now)
    assert alerting.verify_stamp(KEY, st, now + 5) == (True, "OK")
    assert alerting.verify_stamp(KEY, st, now + 500) == (False, "STALE")
    assert alerting.verify_stamp(b"other" * 8, st, now) == (False, "NOT_AUTHENTIC")
    assert alerting.verify_stamp(KEY, st.replace("ok=1", "ok=0"), now) == (False, "NOT_AUTHENTIC")
    assert alerting.verify_stamp(KEY, "garbage", now) == (False, "MALFORMED")
    bad = alerting.make_stamp(KEY, [Alert("BRIDGE_HEARTBEAT_STALE", "critical", "x")], now)
    assert alerting.verify_stamp(KEY, bad, now) == (False, "APP_REPORTS_CRITICAL")
    warn = alerting.make_stamp(KEY, [Alert("KILL_SWITCH_LATCHED", "warning", "x")], now)
    assert (
        alerting.verify_stamp(KEY, warn, now)[0] is True
    )  # a warning alone does not stop the heartbeat


# ------------------------------------------------- notifier against a controlled destination
class _Sink(http.server.BaseHTTPRequestHandler):
    got: list[bytes] = []

    def do_POST(self) -> None:  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        _Sink.got.append(self.rfile.read(n))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *a: object) -> None:
        pass


def test_notifier_sends_code_severity_time_only_and_never_leaks_the_url():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Sink)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/secret-token-path"
    try:
        assert alerting.Notifier(url).send("KILL_SWITCH_TRIPPED", "critical", 123) is True
    finally:
        srv.shutdown()
    body = json.loads(_Sink.got[-1])
    assert set(body) == {"code", "severity", "ts"} and body["code"] == "KILL_SWITCH_TRIPPED"
    dead = alerting.Notifier("http://127.0.0.1:1/secret-token-path")
    with pytest.raises(alerting.NotifyError) as e:
        dead.send("X", "critical")
    assert "secret-token-path" not in str(e.value) and "127.0.0.1" not in str(e.value)
    assert alerting.Notifier(None).send("X", "critical") is False  # unconfigured: nothing sent
    with pytest.raises(alerting.NotifyError):
        alerting.Notifier("http://example.com/x")  # plaintext to a non-loopback host is refused


# ----------------------------------------------------------------- dead-man job with stub binaries
def _deadman(tmp: Path, stamp: str | None, key: bytes = KEY) -> tuple[int, str]:
    bindir = tmp / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp / "curl.log"
    stubs = {
        "nft": "echo ruleset",
        "systemctl": "exit 0",
        "sha256sum": "cat >/dev/null; echo PIN  -",
        "curl": f'for a; do last="$a"; done; echo "$last" >> {log}; exit 0',
    }
    for n, body in stubs.items():
        (bindir / n).write_text("#!/bin/sh\n" + body + "\n")
        (bindir / n).chmod(0o755)
    (tmp / "pin").write_text("PIN  x\n")
    (tmp / "key").write_bytes(key)
    if stamp is not None:
        (tmp / "stamp").write_text(stamp + "\n")
    env = {
        "PATH": f"{bindir}:/usr/bin:/bin",
        "STATUS_FILE": str(tmp / "stamp"),
        "STATUS_KEY_FILE": str(tmp / "key"),
    }
    # the script reads fixed /etc paths: run a copy pointed at test paths
    src = DEADMAN.read_text().replace("/etc/asta-egress/deadman.env", str(tmp / "env"))
    src = src.replace("/etc/asta-egress/ruleset.sha256", str(tmp / "pin"))
    src = src.replace("/proc/sys/net/ipv4/ip_forward", str(tmp / "fwd"))
    (tmp / "env").write_text("DEADMAN_URL=https://hc.example/ping/abc\n")
    (tmp / "fwd").write_text("0\n")
    (tmp / "run.sh").write_text(src)
    r = subprocess.run(  # noqa: S603
        ["/bin/sh", str(tmp / "run.sh")], env=env, capture_output=True, text=True, timeout=30
    )
    url = log.read_text().strip() if log.exists() else ""
    return r.returncode, url


@pytest.mark.skipif(shutil.which("python3") is None, reason="python3 not installed")
def test_deadman_pings_only_for_a_fresh_authentic_healthy_app_stamp(tmp_path: Path):
    good = alerting.make_stamp(KEY, [])
    first = tmp_path / "a"
    first.mkdir()
    rc, url = _deadman(first, good)
    assert (rc, url) == (0, "https://hc.example/ping/abc")  # healthy: plain heartbeat

    def run(name: str, stamp: str | None, key: bytes = KEY) -> tuple[int, str]:
        d = tmp_path / name
        d.mkdir()
        return _deadman(d, stamp, key)

    crit = alerting.make_stamp(KEY, [Alert("BRIDGE_HEARTBEAT_STALE", "critical", "x")])
    for name, stamp in {
        "missing": None,  # app VM dead: no stamp
        "stale": alerting.make_stamp(KEY, [], time.time() - 600),
        "critical": crit,
        "forged": alerting.make_stamp(b"x" * 32, []),
        "garbage": "ts=abc ok=1 codes= mac=00",
    }.items():
        rc, url = run(name, stamp)
        assert rc == 3 and url.endswith("/fail"), f"{name}: rc={rc} url={url}"


# ----------------------------------------------------------------- extended checks (PostgreSQL)
@pytest.mark.db
def test_extended_checks_cover_reconciliation_feed_and_backup(migrated_engine, tmp_path: Path):
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    with migrated_engine.begin() as c:
        c.execute(text("DELETE FROM market_feed_connections"))
        c.execute(text("DELETE FROM operational_alerts"))
        c.execute(text("DELETE FROM incidents WHERE kind = 'RECONCILIATION_MISMATCH'"))
    assert alerting.extended_checks(sessions, tmp_path) == [
        Alert("BACKUP_MISSING", "critical", "no backup file")
    ]
    f = tmp_path / "asta-20260101T000000Z.dump.age"
    f.write_text("x")
    assert alerting.extended_checks(sessions, tmp_path) == []
    os.utime(f, (time.time() - 30 * 3600,) * 2)
    assert [a.code for a in alerting.extended_checks(sessions, tmp_path)] == ["BACKUP_STALE"]
    with migrated_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO market_feed_connections"
                "(venue,state,source,reason,connection_id,since,reconnects,updated_at)"
                " VALUES ('BINANCE','RECONNECTING','LIVE','','',"
                "now() - interval '30 minutes', 0, now())"
            )
        )
    codes = {a.code for a in alerting.extended_checks(sessions, f.parent)}
    assert "MARKET_FEED_LOSS" in codes
    with migrated_engine.begin() as c:
        c.execute(text("DELETE FROM market_feed_connections"))
        c.execute(
            text(
                "INSERT INTO incidents(id,severity,kind,detail,dedupe_key,opened_at)"
                " VALUES (gen_random_uuid(),'CRITICAL','RECONCILIATION_MISMATCH','x','k1',now())"
            )
        )
    assert "RECONCILIATION_DISCREPANCY" in {
        a.code for a in alerting.extended_checks(sessions, f.parent)
    }
    with migrated_engine.begin() as c:
        c.execute(text("DELETE FROM incidents WHERE dedupe_key = 'k1'"))


def test_notifier_destination_is_parsed_not_prefix_matched_and_never_redirected():
    import pytest

    from asta.orders.alerting import Notifier, NotifyError

    for ok in ("https://hc-ping.example/abc", "http://127.0.0.1:9/x", "http://localhost:8080/x"):
        Notifier(ok)
    for bad in (
        "http://localhost.evil.example/x",
        "http://127.0.0.1.evil.example/x",
        "http://user:pw@127.0.0.1/x",
        "http://example.com/x",
        "ftp://example.com/x",
        "https:///nohost",
        "file:///etc/passwd",
    ):
        with pytest.raises(NotifyError):
            Notifier(bad)


# ----------------------------------------------------------------- heartbeat (no-VM dead-man)
class _Ping(http.server.BaseHTTPRequestHandler):
    paths: list[str] = []

    def do_GET(self) -> None:  # noqa: N802
        _Ping.paths.append(self.path)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a: object) -> None:
        pass


def test_heartbeat_pings_the_success_path_when_healthy_and_fail_when_critical(tmp_path: Path):
    from asta.orders import heartbeat as hb

    srv = http.server.HTTPServer(("127.0.0.1", 0), _Ping)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}/ping/secret-token"
    try:
        assert hb.Heartbeat(base).ping(True) is True
        assert hb.Heartbeat(base + "?create=1").ping(False) is True
    finally:
        srv.shutdown()
    assert _Ping.paths[-2:] == [
        "/ping/secret-token",
        "/ping/secret-token/fail?create=1",
    ]  # query kept intact
    assert hb.fail_url("https://hc.example/ping/abc/") == "https://hc.example/ping/abc/fail"


def test_heartbeat_never_leaks_the_url_and_refuses_unsafe_destinations_and_files(tmp_path: Path):
    from asta.orders import heartbeat as hb

    dead = hb.Heartbeat("http://127.0.0.1:1/ping/secret-token")
    with pytest.raises(hb.HeartbeatError) as e:
        dead.ping(True)
    assert "secret-token" not in str(e.value) and "127.0.0.1" not in str(e.value)
    for bad in (
        "http://example.com/x",
        "http://localhost.evil.example/x",
        "ftp://x/y",
        "http://u:p@127.0.0.1/x",
    ):
        with pytest.raises(hb.HeartbeatError):
            hb.Heartbeat(bad)
    f = tmp_path / "url"
    f.write_text("https://hc.example/ping/abc\n")
    f.chmod(0o644)
    with pytest.raises(hb.HeartbeatError):
        hb.load_url_file(str(f))  # group/other readable: refused
    f.chmod(0o400)
    assert hb.load_url_file(str(f)) == "https://hc.example/ping/abc"
    f.chmod(0o600)
    f.write_text("https://a.example/x\nhttps://b.example/y\n")
    f.chmod(0o400)
    with pytest.raises(hb.HeartbeatError):
        hb.load_url_file(str(f))  # exactly one URL


def test_the_monitor_wires_the_heartbeat_from_a_file_never_from_the_url_itself():
    text = (Path(__file__).parents[2] / "src/asta/orders/monitor.py").read_text()
    assert "ASTA_HEARTBEAT_URL_FILE" in text and 'ASTA_HEARTBEAT_URL"' not in text
