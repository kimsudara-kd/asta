"""M3B-FINAL Phase 1: the paper_integrity verifier compares with the frozen M2 evidence (no database needed)."""

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
pi = importlib.import_module("paper_integrity")


def _ok(digest: str) -> dict[str, object]:
    return {
        "replay_steps_verified": 900,
        "reconcile_discrepancies": 0,
        "fills": 35,
        "fills_digest": digest,
    }


def test_the_frozen_snapshot_passes():
    assert pi.verify({"a": _ok("f9d8e770d765ee84"), "b": _ok("be76eb9ef1a917a5")}) == []


def test_any_deviation_fails():
    good = {"a": _ok("f9d8e770d765ee84"), "b": _ok("be76eb9ef1a917a5")}
    assert pi.verify({"a": _ok("f9d8e770d765ee84")})  # missing session
    bad = {**good, "b": {**_ok("be76eb9ef1a917a5"), "reconcile_discrepancies": 1}}
    assert any("reconciliation" in p for p in pi.verify(bad))
    assert any(
        "digests" in p for p in pi.verify({"a": _ok("0" * 16), "b": _ok("be76eb9ef1a917a5")})
    )
    assert any(
        "900" in p
        for p in pi.verify({**good, "a": {**_ok("f9d8e770d765ee84"), "replay_steps_verified": 899}})
    )
