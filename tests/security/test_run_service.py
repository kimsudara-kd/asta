"""The staged service runner: config is strict, secrets must be private, mock mode only, the latch fails closed."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import pytest
from tests.orders.fakes import make_intent

from asta.ipc.unix_socket import UnixSocketChannel
from asta.orders.external_port import ExternalChannelPort
from asta.orders.extwire import Blake2bAuthenticator
from asta.orders.ports import SendOutcome

sys.path.insert(0, str(Path(__file__).parents[2] / "proposals/binance_testnet_signer"))
import credential_service as cs  # type: ignore[import-not-found]  # noqa: E402
import run_service as rsv  # type: ignore[import-not-found]  # noqa: E402

KEY = bytes(range(32))


def wait_listening(path: str, tries: int = 200) -> None:
    """The socket FILE appears at bind(), before listen(): poll by really connecting."""
    import socket as _s
    import time as _t

    for _ in range(tries):
        c = _s.socket(_s.AF_UNIX)
        try:
            c.connect(path)
            return
        except OSError:
            _t.sleep(0.02)
        finally:
            c.close()
    raise AssertionError("service did not start listening")


def write_cfg(d: Path, **over) -> str:
    key = d / "auth.key"
    if key.exists():
        os.chmod(key, 0o600)
    key.write_text(KEY.hex() + "\n")
    os.chmod(key, 0o400)
    cfg = {
        "mode": "mock", "socket": str(d / "c.sock"), "auth_key_file": str(key), "key_id": "k1",
        "replay_db": str(d / "replay.db"), "spend_db": str(d / "spend.db"),
        "enabled_file": str(d / "enabled"), "allowed_uids": [os.getuid()], "senders": ["app"],
        "caps": {"symbols": ["BTC-USDT"], "max_order_notional": "20", "max_day_notional": "200"},
    }  # fmt: skip
    cfg.update(over)
    p = d / "cred.json"
    p.write_text(json.dumps(cfg))
    return str(p)


@pytest.fixture
def short_dir():
    d = Path(tempfile.mkdtemp(dir="/tmp"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_testnet_mode_and_malformed_configs_are_refused(short_dir):
    bad_configs: list[dict[str, Any]] = [{"mode": "testnet"}, {"mode": "live"}, {"senders": []}]
    for over in bad_configs:
        with pytest.raises(rsv.ConfigError):
            rsv.load_config(write_cfg(short_dir, **over))
    p = Path(write_cfg(short_dir))
    cfg = json.loads(p.read_text())
    del cfg["replay_db"]
    p.write_text(json.dumps(cfg))
    with pytest.raises(rsv.ConfigError, match="missing"):
        rsv.load_config(str(p))
    assert rsv.main(["run_service.py", "--config", str(p)]) == 2  # the entry point refuses to start


def test_secret_files_must_be_private_and_well_formed(short_dir):
    p = short_dir / "k"
    p.write_text(KEY.hex())
    for mode in (0o644, 0o604, 0o460, 0o666):
        os.chmod(p, mode)
        with pytest.raises(rsv.ConfigError):
            rsv.read_secret_file(str(p))
    os.chmod(p, 0o400)
    assert rsv.read_secret_file(str(p)) == KEY
    for bad in ("zz" * 32, "ab" * 31, ""):
        os.chmod(p, 0o600)
        p.write_text(bad)
        os.chmod(p, 0o400)
        with pytest.raises(rsv.ConfigError):
            rsv.read_secret_file(str(p))


def test_the_file_latch_fails_closed(short_dir):
    latch = short_dir / "kill"
    k = rsv.FileKill(str(latch))
    assert k.active() is False
    latch.write_text("")
    assert k.active() is True
    latch.unlink()
    assert k.active() is False
    assert (
        rsv.FileKill("/proc/\x00bad").active() is True
    )  # a path that cannot even be checked is LATCHED


def test_the_built_service_serves_a_real_socket_and_a_restart_cannot_replay(short_dir):
    cfg = rsv.load_config(write_cfg(short_dir))
    (short_dir / "enabled").write_text(
        str(1_800_000_000 + 3600)
    )  # testnet execution enabled for the next hour
    auth = Blake2bAuthenticator({"k1": KEY})

    def run(clock_t: float) -> SendOutcome:
        built = rsv.build(cfg, clock=lambda: clock_t)
        stop = threading.Event()
        th = threading.Thread(
            target=cs.serve_unix, args=(built.socket_path, built.service, stop), daemon=True
        )
        th.start()
        wait_listening(built.socket_path)
        try:
            p = ExternalChannelPort(
                UnixSocketChannel(built.socket_path, 3.0), auth, "app", "k1", lambda: clock_t
            )
            i = make_intent(
                1,
                order_type="LIMIT",
                quote_notional=None,
                quantity=D("0.0001"),
                limit_price=D("50000"),
            )
            return p.send(i)
        finally:
            stop.set()
            th.join(3)

    assert run(1_800_000_000.0) is SendOutcome.ACCEPTED
    # a restarted service on the same replay file; a port with the same clock reuses the sequence
    # number, which the durable store refuses (the reply is then unauthenticated: UNKNOWN)
    assert run(1_800_000_000.0) is SendOutcome.UNKNOWN
    assert os.stat(cfg["replay_db"]).st_mode & 0o077 == 0  # the replay file is private


def test_the_runner_never_reads_the_environment_or_the_network():
    import ast

    text = (
        Path(__file__).parents[2] / "proposals/binance_testnet_signer/run_service.py"
    ).read_text()
    tree = ast.parse(text)
    used = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | {
        n.id for n in ast.walk(tree) if isinstance(n, ast.Name)
    }
    assert not used & {"environ", "getenv", "urlopen", "socket", "HTTPSConnection", "request"}
    mods = {
        a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    }
    mods |= {
        n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
    }
    assert not mods & {"urllib", "http", "socket", "ssl", "requests", "httpx"}
