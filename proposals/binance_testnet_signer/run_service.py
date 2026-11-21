"""STAGED entry point of the credential-side service (outside src/). Not installed, not wired.

    python run_service.py --config /path/to/cred.json

``"mode": "mock"``: the in-process mock exchange and a SYNTHETIC signer (dry runs, drills). ``"mode": "testnet"``
(step F): the real Binance Spot Testnet through the pinned HTTPS transport and the key-file signer; it refuses to start
without a fresh PASSING step E probe stamp for exactly that key.
Everything is read from the config file (never from environment variables, never from the command line); secrets live in
files that must not be readable by other users. The request-authentication key is a file of 64 hex characters.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

# Installed layout: <root>/service/run_service.py and <root>/asta/...; run with ``python -I`` (which ignores
# PYTHONPATH), so both locations are added here. In the repository this is harmless.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE))

from connector import BinanceTestnetPort  # noqa: E402
from credential_service import Caps, CredentialService, serve_unix  # noqa: E402
from mock_exchange import MockTestnetExchange  # noqa: E402
from replay_store import FileReplayStore, FileSpendStore  # noqa: E402
from signer import SignerError, SyntheticEd25519Signer  # noqa: E402

from asta.orders.extwire import Blake2bAuthenticator, RequestReceiver  # noqa: E402

REQUIRED = (
    "mode", "socket", "auth_key_file", "key_id", "replay_db", "spend_db", "enabled_file", "allowed_uids",
    "senders", "caps",
)  # fmt: skip


class ConfigError(Exception):
    pass


class FileKill:
    """Credential-side latch: the file existing (or being unreadable) means LATCHED. Fail closed."""

    def __init__(self, path: str) -> None:
        self._path = path

    def active(self) -> bool:
        try:
            os.stat(self._path)
            return True  # the latch file exists
        except FileNotFoundError:
            return False
        except (OSError, ValueError):
            return True  # cannot even check: treat as latched


class FileEnabled:
    """Credential-side view of the runtime enablement. The file holds one integer: the unix time until which testnet
    execution is enabled (written by the owner's tooling from the database state). Missing, unreadable, malformed,
    expired or more than 24 h 1 min ahead means NOT enabled. Fail closed."""

    def __init__(self, path: str, clock: Any = time.time) -> None:
        self._path, self._clock = path, clock

    def active(self) -> bool:
        try:
            until = int(Path(self._path).read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return False
        now = self._clock()
        return now < until <= now + 24 * 3600 + 60


def read_secret_file(path: str) -> bytes:
    st = os.stat(path)
    if st.st_mode & 0o007 or st.st_mode & 0o020:
        raise ConfigError(f"{path}: must not be readable by others nor writable by the group")
    if st.st_uid not in (os.getuid(), 0):
        raise ConfigError(f"{path}: must be owned by this user (or root)")
    raw = Path(path).read_text(encoding="ascii").strip()
    try:
        key = bytes.fromhex(raw)
    except ValueError as exc:
        raise ConfigError(f"{path}: not 64 hex characters") from exc
    if len(key) != 32:
        raise ConfigError(f"{path}: not 64 hex characters")
    return key


@dataclass
class Built:
    service: CredentialService
    socket_path: str
    allowed_uids: frozenset[int]


def load_config(path: str) -> dict[str, Any]:
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    missing = [k for k in REQUIRED if k not in cfg]
    if missing:
        raise ConfigError(f"missing config keys: {missing}")
    if cfg["mode"] not in ("mock", "testnet"):
        raise ConfigError("mode must be 'mock' or 'testnet'")
    if cfg["mode"] == "testnet":
        block = cfg.get("testnet")
        need = ("private_key_file", "api_key_file", "probe_stamp_file")
        if not isinstance(block, dict) or any(k not in block for k in need):
            raise ConfigError(f"mode 'testnet' needs a 'testnet' block with {list(need)}")
    if not isinstance(cfg["senders"], list) or not cfg["senders"]:
        raise ConfigError("senders must be a non-empty list")
    uids = cfg["allowed_uids"]
    if (
        not isinstance(uids, list)
        or not uids
        or not all(isinstance(u, int) and u >= 0 for u in uids)
    ):
        raise ConfigError("allowed_uids must be a non-empty list of user ids (the app user)")
    return cfg  # type: ignore[no-any-return]


def build(cfg: dict[str, Any], clock: Any = time.time) -> Built:
    key = read_secret_file(cfg["auth_key_file"])
    auth = Blake2bAuthenticator({cfg["key_id"]: key})
    rx = RequestReceiver(
        auth,
        frozenset(cfg["senders"]),
        FileReplayStore(cfg["replay_db"]),
        allowed_key_ids=frozenset({cfg["key_id"]}),
    )
    c = cfg["caps"]
    caps = Caps(
        symbols=frozenset(c.get("symbols", ["BTC-USDT", "ETH-USDT"])),
        max_order_notional=Decimal(str(c.get("max_order_notional", "20"))),
        max_day_notional=Decimal(str(c.get("max_day_notional", "200"))),
        spend=FileSpendStore(cfg["spend_db"]),
    )
    kill = FileKill(cfg.get("kill_file", cfg["socket"] + ".kill"))

    def ms() -> int:
        return int(clock() * 1000)

    if cfg["mode"] == "mock":
        synth = SyntheticEd25519Signer()  # mock mode only
        port = BinanceTestnetPort(
            synth, MockTestnetExchange(synth.public_key_b64(), ms), ms, "SYNTHETIC-KEY-ID"
        )
    else:  # step F: the real testnet. Needs a fresh PASSING probe for exactly this key (E before F).
        from file_signer import FileEd25519Signer
        from https_transport import HttpsTransport
        from probe import ProbeConfigError, _ssl_context, read_private_line, read_stamp

        t_cfg = cfg["testnet"]
        try:
            signer = FileEd25519Signer(t_cfg["private_key_file"])
            fingerprint = repr(signer).split("=")[1].rstrip(">")
            read_stamp(t_cfg["probe_stamp_file"], fingerprint, clock())
            api_key = read_private_line(t_cfg["api_key_file"])
        except (ProbeConfigError, SignerError, OSError) as exc:
            raise ConfigError(f"testnet mode refused: {exc}") from exc
        port = BinanceTestnetPort(signer, HttpsTransport(_ssl_context(t_cfg)), ms, api_key)
    enabled = FileEnabled(cfg["enabled_file"], clock)
    svc = CredentialService(
        rx, auth, port, caps, kill.active, clock, cfg["key_id"], execution_enabled=enabled.active
    )
    return Built(svc, cfg["socket"], frozenset(cfg["allowed_uids"]))


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] != "--config":
        print("usage: run_service.py --config FILE", file=sys.stderr)
        return 2
    try:
        built = build(load_config(argv[2]))
    except (ConfigError, OSError, ValueError) as exc:
        print(f"refusing to start: {exc}", file=sys.stderr)
        return 2
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    print("credential service (MOCK mode) listening", file=sys.stderr)
    serve_unix(built.socket_path, built.service, stop, allowed_uids=built.allowed_uids)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
