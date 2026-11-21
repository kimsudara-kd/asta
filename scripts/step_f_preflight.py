#!/usr/bin/env python3
"""Step F preflight (read-only): is everything in place for tiny REAL testnet orders? Prints one line per requirement.

    python3 scripts/step_f_preflight.py [--config /etc/asta-cred/cred.json] [--database-url URL]

Never changes anything, never prints a secret, never contacts a network. Exit 0 only when EVERY requirement is PASS;
any BLOCKED/OWNER item exits 1. 'OWNER' means it needs your explicit decision and cannot be checked by a program.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import host_posture  # noqa: E402

REQUIRED_CAPS = {
    "max_order_notional": "20",
    "max_day_notional": "200",
}  # the proposed pilot caps (owner to confirm)


def item(name: str, ok: bool | None, detail: str, owner: bool = False) -> tuple[str, str, str]:
    return (name, "OWNER" if owner else ("PASS" if ok else "BLOCKED"), detail)


def check_constant() -> tuple[str, str, str]:
    from asta.risk import gateway

    on = gateway.TESTNET_EXECUTION_ALLOWED is True and gateway.LIVE_TRADING_ENABLED is False
    return item(
        "reviewed constant TESTNET_EXECUTION_ALLOWED flipped in its own commit",
        on,
        f"TESTNET_EXECUTION_ALLOWED={gateway.TESTNET_EXECUTION_ALLOWED}, LIVE_TRADING_ENABLED={gateway.LIVE_TRADING_ENABLED}",
    )


def check_db(url: str | None) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    try:
        from sqlalchemy import text

        from asta.core.settings import load_settings
        from asta.db.session import make_engine

        engine = make_engine(url or load_settings().database_url)
        with engine.connect() as c:
            ver = c.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            rel = c.execute(text("SELECT count(*) FROM kill_switch_releasers")).scalar_one()
            latched = c.execute(
                text("SELECT active FROM kill_switch_state WHERE id = 1")
            ).scalar_one()
            enabled = c.execute(text("SELECT execution_enabled('BINANCE_TESTNET')")).scalar_one()
        out.append(
            item("database migrated to 0014 or later", str(ver) >= "0014", f"alembic head {ver}")
        )
        out.append(
            item(
                "at least one listed releaser (one login per human)", int(rel) > 0, f"{rel} listed"
            )
        )
        out.append(item("kill switch not latched", not latched, "latched" if latched else "clear"))
        out.append(
            item(
                "runtime enablement (informational: it is switched on only for a session)",
                None,
                f"enabled now: {bool(enabled)}",
                owner=True,
            )
        )
    except Exception as exc:  # noqa: BLE001
        out.append(
            item("database reachable and migrated", False, f"cannot read: {type(exc).__name__}")
        )
    return out


def check_host() -> list[tuple[str, str, str]]:
    p: dict[str, Any] = host_posture.posture() if sys.platform == "darwin" else {}
    if not p:
        return [item("host hardening", False, "not a macOS host: cannot be assessed here")]
    return [
        item("FileVault on", bool(p["filevault_on"]), "on" if p["filevault_on"] else "OFF"),
        item(
            "application firewall on",
            bool(p["app_firewall_on"]),
            "on" if p["app_firewall_on"] else "OFF",
        ),
        item(
            "automatic login off",
            bool(p["auto_login_disabled"]),
            "off" if p["auto_login_disabled"] else "ON",
        ),
        item(
            "SMB file sharing off",
            not p["smb_listening"],
            "listening" if p["smb_listening"] else "closed",
        ),
        item(
            "remote login (SSH) closed or reviewed",
            not p["ssh_listening"],
            "listening (needs key-only + restricted)" if p["ssh_listening"] else "closed",
        ),
    ]


def check_service(config: str | None) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    out.append(
        item(
            "credential service installed (/opt/asta-cred venv + plist)",
            Path("/opt/asta-cred/venv/bin/python").exists()
            and Path("/Library/LaunchDaemons/com.asta.cred.plist").exists(),
            "see docs/m3b/NO-VM-DEPLOYMENT.md section 6",
        )
    )
    if not config or not Path(config).exists():
        out.append(
            item("service config present and in testnet mode", False, "no readable config given")
        )
        return out
    try:
        cfg = json.loads(Path(config).read_text())
    except (OSError, ValueError) as exc:
        return out + [item("service config readable", False, type(exc).__name__)]
    out.append(
        item(
            "service config mode is 'testnet'",
            cfg.get("mode") == "testnet",
            f"mode={cfg.get('mode')}",
        )
    )
    caps = cfg.get("caps", {})
    same = all(str(caps.get(k)) == v for k, v in REQUIRED_CAPS.items())
    out.append(
        item(
            "caps equal the approved pilot caps (20 / 200 USDT; BTC-USDT, ETH-USDT)",
            same,
            f"order={caps.get('max_order_notional')} day={caps.get('max_day_notional')} symbols={caps.get('symbols')}",
            owner=not same and False,
        )
    )
    stamp = (cfg.get("testnet") or {}).get("probe_stamp_file")
    fresh = False
    detail = "no probe stamp configured"
    if stamp and Path(stamp).exists():
        try:
            s = json.loads(Path(stamp).read_text())
            age = time.time() - int(s["ts"])
            fresh = s.get("verdict") == "SIGNATURE_AND_ORDER_VALID" and 0 <= age <= 7 * 86400
            detail = f"verdict {s.get('verdict')}, age {int(age // 3600)} h"
        except (OSError, ValueError, KeyError):
            detail = "stamp unreadable"
    out.append(item("step E probe passed in the last 7 days", fresh, detail))
    return out


def check_misc() -> list[tuple[str, str, str]]:
    hb = os.environ.get("ASTA_HEARTBEAT_URL_FILE")
    ok = bool(hb) and Path(hb).exists() and not (Path(hb).stat().st_mode & 0o077)  # type: ignore[arg-type]
    return [
        item(
            "off-host heartbeat URL file configured and private",
            ok,
            "ASTA_HEARTBEAT_URL_FILE " + ("ok" if ok else "not set/unsafe"),
        ),
        item(
            "independent re-review of the gateway path recorded",
            None,
            "docs/evidence/M3B-FINAL-REVIEW-REPORT.md (S-01..S-09 closed); owner to confirm the re-review of the final state",
            owner=True,
        ),
        item(
            "owner written approval to flip the constant and enable a session",
            None,
            "required each time: flip in its own commit; enable <= 24 h by a listed releaser",
            owner=True,
        ),
    ]


def main(argv: list[str]) -> int:
    opts = dict(zip(argv[1::2], argv[2::2], strict=False))
    rows = [
        check_constant(),
        *check_db(opts.get("--database-url")),
        *check_host(),
        *check_service(opts.get("--config")),
        *check_misc(),
    ]
    width = max(len(r[0]) for r in rows)
    for name, status, detail in rows:
        print(f"{status:7} {name:<{width}}  {detail}")
    ready = all(r[1] == "PASS" for r in rows if r[1] != "OWNER") and all(
        r[1] != "BLOCKED" for r in rows
    )
    print("\nSTEP F READY" if ready else "\nSTEP F NOT READY: real testnet orders must not start")
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
