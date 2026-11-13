"""Linux entry point of the paper supervisor (M3A-MAC-01). The Windows supervisor is NOT modified.

    python scripts/paper_supervisor_posix.py        # on the Linux ARM64 VM, run by systemd

It reuses ``paper_supervisor.main`` unchanged and replaces only its two Windows-specific helpers
(``_pid_running`` via ``tasklist`` and ``kill_orphan_services`` via PowerShell/``taskkill``) with
``/proc``-based equivalents (no new dependency). The "do not idle-sleep" request is already
suppressed on non-Windows systems by the base code. systemd (``Restart=always``) replaces the
Windows Task Scheduler keep-alive; the single-instance lock file is kept as a second layer.

SAFETY: nothing here uses a signal-0 liveness probe through ``os.kill`` (on Windows that terminates the process) and the
orphan cleanup only matches an explicit marker in a process command line, with an injectable
``proc_root`` and ``kill`` so tests never touch real processes.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import paper_supervisor as base  # noqa: E402  (the unchanged Windows-tested supervisor logic)

MARKER = "asta.paper_service"


def pid_running(pid: int, proc_root: Path = Path("/proc")) -> bool:
    d = proc_root / str(pid)
    if not d.exists():
        return False
    try:
        state = (d / "stat").read_text().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return True
    return state != "Z"  # a zombie is not running


def find_by_cmdline(
    marker: str, proc_root: Path = Path("/proc"), exclude: tuple[int, ...] = ()
) -> list[int]:
    pids: list[int] = []
    for d in proc_root.iterdir():
        if not d.name.isdigit() or int(d.name) in exclude:
            continue
        try:
            cmd = (d / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if marker in cmd:
            pids.append(int(d.name))
    return sorted(pids)


def kill_orphans(
    marker: str = MARKER,
    proc_root: Path = Path("/proc"),
    kill: Callable[[int, int], None] = os.kill,
    grace_s: float = 5.0,
) -> list[int]:
    """Terminate ASTA service processes left by a previous supervisor (ASTA-specific marker only)."""
    pids = find_by_cmdline(marker, proc_root, exclude=(os.getpid(),))
    for pid in pids:
        try:
            kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.time() + grace_s
    while time.time() < deadline and any(pid_running(p, proc_root) for p in pids):
        time.sleep(0.2)
    for pid in pids:
        if pid_running(pid, proc_root):
            try:
                kill(pid, getattr(signal, "SIGKILL", 9))
            except ProcessLookupError:
                pass
    return pids


def main() -> int:
    if os.name != "posix":
        print(
            "paper_supervisor_posix.py is for the Linux VM; use paper_supervisor.py on Windows",
            file=sys.stderr,
        )
        return 2
    base._pid_running = pid_running  # type: ignore[attr-defined]
    base.kill_orphan_services = kill_orphans  # type: ignore[attr-defined]
    return int(base.main())


if __name__ == "__main__":
    raise SystemExit(main())
