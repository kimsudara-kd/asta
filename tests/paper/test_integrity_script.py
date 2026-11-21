"""M3B-FINAL Phase 1: the paper_integrity verifier compares with the frozen M2 evidence (no database needed)."""

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
pi = importlib.import_module("paper_integrity")


CONTENT = {"a": "970891b4aef19133", "b": "4a7f40147ac8df6d"}


def _ok(digest: str, content: str = "") -> dict[str, object]:
    return {
        "content_digest": content,
        "replay_steps_verified": 900,
        "reconcile_discrepancies": 0,
        "fills": 35,
        "fills_digest": digest,
    }


def _frozen(monkeypatch):
    monkeypatch.setattr(pi, "CONTENT_DIGESTS", CONTENT)
    return {"a": _ok("f9d8e770d765ee84", CONTENT["a"]), "b": _ok("be76eb9ef1a917a5", CONTENT["b"])}


def test_the_frozen_snapshot_passes(monkeypatch):
    assert pi.verify(_frozen(monkeypatch)) == []


def test_a_changed_price_or_fee_is_caught_even_when_the_fill_ids_are_unchanged(monkeypatch):
    snap = _frozen(monkeypatch)
    snap["a"]["content_digest"] = "0" * 16  # same ids, different price/qty/fee
    assert any("CONTENT" in p for p in pi.verify(snap))
    swapped = _frozen(monkeypatch)  # digests attributed to the wrong session
    swapped["a"]["content_digest"], swapped["b"]["content_digest"] = CONTENT["b"], CONTENT["a"]
    assert any("CONTENT" in p for p in pi.verify(swapped))


def test_any_deviation_fails(monkeypatch):
    good = _frozen(monkeypatch)
    assert pi.verify({"a": _ok("f9d8e770d765ee84")})  # missing session
    bad = {**good, "b": {**good["b"], "reconcile_discrepancies": 1}}
    assert any("reconciliation" in p for p in pi.verify(bad))
    assert any(
        "digests" in p for p in pi.verify({"a": _ok("0" * 16, CONTENT["a"]), "b": good["b"]})
    )
    assert any(
        "900" in p for p in pi.verify({**good, "a": {**good["a"], "replay_steps_verified": 899}})
    )
