"""Host-side verification of the egress boundary incl. restart and fail-closed behaviour (M3A-R1).

    python infra/egress/verify_boundary.py [out.json]

Runs, against the deployed ASTA-only containers (never any other project's):
  baseline -> proxy restart -> gate restart (+ dependents) -> gate stopped (fail closed) -> recovery
Each phase runs the 78 negative checks from the protected container and the compromised-proxy
checks from an unprivileged process inside the proxy's own network namespace, and records the
kernel drop counters. It does NOT restart Docker Desktop / the VM (shared with other projects).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMPOSE = ["docker", "compose", "-f", str(HERE / "docker-compose.egress.yml"), "--profile", "lab"]
ENV = {**os.environ, "MSYS_NO_PATHCONV": "1"}


def sh(*args: str, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=ENV)  # noqa: S603


def wait_healthy(names: list[str], secs: int = 120) -> bool:
    end = time.time() + secs
    while time.time() < end:
        ok = True
        for n in names:
            r = sh("docker", "inspect", "-f", "{{.State.Health.Status}}", n)
            ok &= r.stdout.strip() == "healthy"
        if ok:
            return True
        time.sleep(3)
    return False


def counters() -> dict[str, str]:
    r = sh("docker", "exec", "asta-egress-gate", "nft", "list", "ruleset")
    return {
        ln.strip().split(" drop")[0]: ln.strip()
        for ln in r.stdout.splitlines()
        if "counter packets" in ln and "drop" in ln
    }


def run_suites() -> dict[str, object]:
    out: dict[str, object] = {}
    for key, ctr, script in (
        ("negative_tests", "asta-protected-probe", "/app/negative_tests.py"),
        ("compromised_proxy_tests", "asta-compromised-proxy-sim", "/app/gate_tests.py"),
    ):
        r = sh("docker", "exec", ctr, "python", script)
        try:
            d = json.loads(r.stdout)
            out[key] = {
                "total": d["total"],
                "failed": d["failed"],
                "failed_tests": [x for x in d["results"] if not x["pass"]],
            }
        except Exception:  # noqa: BLE001
            out[key] = {"error": (r.stdout + r.stderr)[:300]}
    out["drop_counters"] = counters()
    return out


def probe_via_proxy() -> str:
    code = (
        "import socket;s=socket.create_connection(('asta-egress-proxy',3128),4);"
        "s.sendall(b'CONNECT data-api.binance.vision:443 HTTP/1.1\\r\\n\\r\\n');"
        "print(s.recv(40).split(b'\\r\\n')[0].decode())"
    )
    r = sh("docker", "exec", "asta-protected-probe", "python", "-c", code, timeout=30)
    return (r.stdout.strip() or r.stderr.strip()[-120:])[:120]


def main() -> int:
    report: dict[str, object] = {"started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    ok_all = True
    report["baseline"] = run_suites()

    sh("docker", "restart", "asta-egress-proxy")
    report["proxy_restart_healthy"] = wait_healthy(["asta-egress-proxy"])
    report["after_proxy_restart"] = run_suites()

    # gate restart: the proxy shares its namespace, so dependents are recreated (compose handles it)
    sh("docker", "restart", "asta-egress-gate")
    time.sleep(5)
    report["proxy_state_right_after_gate_restart"] = probe_via_proxy()
    sh(*COMPOSE, "up", "-d")
    sh(
        "docker",
        "restart",
        "asta-egress-proxy",
        "asta-protected-probe",
        "asta-compromised-proxy-sim",
    )
    report["gate_restart_healthy"] = wait_healthy(["asta-egress-gate", "asta-egress-proxy"])
    time.sleep(5)
    report["after_gate_restart"] = run_suites()

    # fail closed: gate down -> nothing may pass
    sh("docker", "stop", "asta-egress-gate")
    time.sleep(3)
    report["gate_stopped_probe_result"] = probe_via_proxy()
    sh(*COMPOSE, "up", "-d")
    sh(
        "docker",
        "restart",
        "asta-egress-proxy",
        "asta-protected-probe",
        "asta-compromised-proxy-sim",
    )
    report["recovered_healthy"] = wait_healthy(["asta-egress-gate", "asta-egress-proxy"])
    time.sleep(5)
    report["after_recovery"] = run_suites()
    report["probe_after_recovery"] = probe_via_proxy()

    for phase in ("baseline", "after_proxy_restart", "after_gate_restart", "after_recovery"):
        for suite in ("negative_tests", "compromised_proxy_tests"):
            res = report[phase][suite]  # type: ignore[index]
            ok_all &= res.get("failed") == 0 and "error" not in res
    ok_all &= str(report["gate_stopped_probe_result"]).find("200") < 0  # fail closed
    ok_all &= "200" in str(report["probe_after_recovery"])
    report["all_passed"] = bool(ok_all)
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "verify_boundary.out.json"
    path.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(
        json.dumps(
            {
                k: v
                for k, v in report.items()
                if k
                in (
                    "all_passed",
                    "gate_stopped_probe_result",
                    "probe_after_recovery",
                    "proxy_state_right_after_gate_restart",
                )
            },
            indent=1,
        )
    )
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
