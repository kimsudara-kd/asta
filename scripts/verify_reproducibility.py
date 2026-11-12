"""M1-R1 workstream B on the REAL stored datasets (offline; reads data/store only).

For each full-period configuration used in the original research run:
  1. run it twice in this process and compare every artefact digest (orders+events, fills, ledger,
     equity, risk events, checkpoints) and the performance report;
  2. compare the result checksum with the checksum recorded in docs/evidence/m1/baseline-comparison.json
     (the ORIGINAL run, produced earlier by a separate process);
  3. run the independent fill-based reconciliation of balances / equity / P&L;
  4. report risk verdicts next to returns (profitable runs that breached are called out).
Then one run in a fresh process with a different PYTHONHASHSEED, and a look-ahead truncation check.

Usage: python scripts/verify_reproducibility.py [--out docs/evidence/m1-r1]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import time
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_research as rr  # noqa: E402

from asta.research.data.profiles import RuleProfile  # noqa: E402
from asta.research.runner import canonical, run_backtest  # noqa: E402
from asta.research.sim.risk import SimRiskLimits  # noqa: E402
from asta.research.validation.audit import digests, independent_audit  # noqa: E402

SYMS = rr.SYMS
SPECS = {
    "all_cash": ("all_cash", {}, "default"),
    "passive_hold_equal_weight_300": (
        "passive_hold",
        {"symbols": list(SYMS), "notional_per_symbol": "100"},
        "default",
    ),
    "passive_hold_BTC_only_100": (
        "passive_hold",
        {"symbols": ["BTC-USDT"], "notional_per_symbol": "100"},
        "default",
    ),
    "passive_hold_reference_full_capital": (
        "passive_hold",
        {"symbols": list(SYMS), "notional_per_symbol": "333"},
        "unconstrained",
    ),
    "sma_cross_default": ("sma_cross", {"symbols": list(SYMS)}, "default"),
    "regime_grid_default": ("regime_grid", {}, "default"),
    "trend_pullback_default": ("trend_pullback", {}, "default"),
}


def log(m: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def build(name, data, rules):
    sid, params, risk = SPECS[name]
    lim = SimRiskLimits.default() if risk == "default" else SimRiskLimits.unconstrained_reference()
    return rr.base_config(data, rules, sid, params, label=name).replace(risk=lim, warmup_bars=0)


def load(args):
    data = rr.load_data(Path(args.store))
    rules_file = sorted(glob.glob("data/import/rules_binance_*.json"))[-1]
    rules = tuple(
        RuleProfile.from_json(p) for p in json.loads(Path(rules_file).read_text(encoding="utf-8"))
    )
    return data, rules


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/evidence/m1-r1")
    ap.add_argument("--store", default="data/store")
    ap.add_argument("--emit", default=None, help="(internal) print digests of one named run")
    args = ap.parse_args()
    data, rules = load(args)
    if args.emit:
        r = run_backtest(build(args.emit, data, rules), data)
        print("DIGEST " + json.dumps([r.result_checksum, digests(r.result)]))
        return 0

    original = json.loads(Path("docs/evidence/m1/baseline-comparison.json").read_text("utf-8"))[
        "result_checksums"
    ]
    report: dict = {"runs": {}, "fresh_process": {}, "lookahead": {}}
    ok_all = True
    for name in SPECS:
        log(f"{name}: run x2")
        cfg = build(name, data, rules)
        a = run_backtest(cfg, data)
        b = run_backtest(cfg, data)
        da, db = digests(a.result), digests(b.result)
        same = da == db and a.result_checksum == b.result_checksum
        perf_same = canonical(a.performance.to_json()) == canonical(b.performance.to_json())
        orig = original.get(name)
        audit = independent_audit(a.result, cfg.starting_cash, "USDT", a.performance.returns)
        ret = a.performance.returns
        net = Decimal(str(ret["total_net_pnl"]))
        entry = {
            "repeat_identical": same and perf_same,
            "artefact_digests_equal": {k: da[k] == db[k] for k in da},
            "result_checksum": a.result_checksum,
            "original_result_checksum": orig,
            "matches_original_run": a.result_checksum == orig,
            "counts": {
                "orders": len(a.result.orders),
                "fills": len(a.result.fills),
                "ledger_entries": len(a.result.ledger.entries),
                "equity_points": len(a.result.equity),
                "risk_events": len(a.result.risk_events),
            },
            "net_pnl": str(net),
            "risk_verdict": a.result.risk_verdict,
            "status": a.result.status,
            "profitable_but_breached": net > 0 and a.result.risk_verdict == "BREACHED",
            "audit": [{"check": c.name, "ok": c.ok, "detail": c.detail} for c in audit],
            "audit_ok": all(c.ok for c in audit),
        }
        entry["verdict_reported_with_return"] = (
            "BREACHED" in (entry["risk_verdict"],) or not entry["profitable_but_breached"]
        )
        report["runs"][name] = entry
        ok = entry["repeat_identical"] and entry["audit_ok"] and entry["matches_original_run"]
        ok_all &= ok
        log(
            f"  repeat={same} orig_match={entry['matches_original_run']} audit={entry['audit_ok']} net={net} {a.result.risk_verdict}"
        )

    # fresh process, different hash seed
    name = "regime_grid_default"
    log("fresh process with PYTHONHASHSEED=424242")
    env = {**os.environ, "PYTHONHASHSEED": "424242", "PYTHONUTF8": "1"}
    p = subprocess.run(  # noqa: S603  (fixed argv: this interpreter + this script)
        [sys.executable, __file__, "--emit", name, "--store", args.store],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    line = next(ln for ln in p.stdout.splitlines() if ln.startswith("DIGEST "))
    chk, dig = json.loads(line[7:])
    base = digests(run_backtest(build(name, data, rules), data).result)
    report["fresh_process"] = {
        "run": name,
        "pythonhashseed": "424242",
        "checksum_equal": chk == report["runs"][name]["result_checksum"],
        "artefact_digests_equal": {k: dig[k] == base[k] for k in base},
    }
    ok_all &= report["fresh_process"]["checksum_equal"] and all(
        report["fresh_process"]["artefact_digests_equal"].values()
    )

    # look-ahead on real data: truncating the data after T changes nothing before T
    log("look-ahead truncation on real data")
    for sid in ("regime_grid_default", "sma_cross_default", "trend_pullback_default"):
        full = run_backtest(build(sid, data, rules), data)
        cut = rr.START + timedelta(days=540)
        short = run_backtest(build(sid, data, rules).replace(period_end=cut), data)

        def key(f):
            return (f.order_id, f.symbol, f.side, f.ts, f.price, f.quantity, f.fee_amount)

        a = [key(f) for f in full.result.fills if f.ts < cut]
        b = [key(f) for f in short.result.fills if f.ts < cut]
        ea = [(e.ts, e.equity) for e in full.result.equity if e.ts < cut]
        eb = [(e.ts, e.equity) for e in short.result.equity if e.ts < cut]
        n = min(len(ea), len(eb))
        res = {
            "fills_before_cut": len(a),
            "fills_identical": a == b,
            "equity_points_compared": n,
            "equity_identical": ea[:n] == eb[:n] and abs(len(ea) - len(eb)) <= 1,
        }
        report["lookahead"][sid] = res
        ok_all &= res["fills_identical"] and res["equity_identical"] and len(a) > 0
        log(f"  {sid}: {res}")

    report["overall_pass"] = bool(ok_all)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "reproducibility-real-data.json").write_text(
        json.dumps(report, indent=1, sort_keys=True), encoding="utf-8"
    )
    log(f"OVERALL {'PASS' if ok_all else 'FAIL'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
