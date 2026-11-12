"""M1-R1 Workstream A: persistence of completed research studies (verbatim, provenance, no dupes)."""
# ruff: noqa: F811

import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import text

from asta.db import research_import as ri
from asta.db import research_store as rs
from asta.db.audit_store import recent_audit
from tests.integration.test_api import sign_in
from tests.integration.test_research_api import client, post  # noqa: F401, F811

pytestmark = pytest.mark.db
EVIDENCE = Path(__file__).resolve().parents[2] / "docs" / "evidence" / "m1"
EXPECTED = 16  # 6 walk-forward + 3 sensitivity + 3 fixed windows + 3 holdout + 1 baseline
IGN = shutil.ignore_patterns("screenshots", "*.log")


def count(db) -> int:
    return int(db.execute(text("select count(*) from research_validations")).scalar_one())


def test_import_persists_every_study_verbatim_with_provenance(db):
    rep = ri.import_studies(db, EVIDENCE, "tester")
    db.commit()
    assert len(rep.inserted) == EXPECTED and not rep.skipped_duplicate
    assert count(db) == EXPECTED
    rows = db.execute(text("select * from research_validations")).mappings().all()
    kinds = sorted({r["kind"] for r in rows})
    assert kinds == ["BASELINE_COMPARISON", "HOLDOUT", "SENSITIVITY", "WALK_FORWARD", "WINDOWS"]
    for r in rows:
        assert r["content_hash"] and r["source_datasets"] and r["generated_at"]
        assert r["source_artifact"] and r["research_grade"] is True
        assert r["sample_type"] in ri.SAMPLE_TYPES
        assert {"BTC-USDT", "ETH-USDT", "SOL-USDT"} <= set(r["source_datasets"])
        assert all(len(d["checksum"]) == 64 for d in r["source_datasets"].values())
        if r["strategy_id"] != "ALL":
            assert r["strategy_version"]
    by = {(r["kind"], r["strategy_id"]): r for r in rows if r["kind"] != "WALK_FORWARD"}
    assert by[("HOLDOUT", "regime_grid")]["sample_type"] == "FINAL_HOLDOUT"
    assert by[("SENSITIVITY", "regime_grid")]["sample_type"] == "IN_SAMPLE"
    wf = [r for r in rows if r["kind"] == "WALK_FORWARD"]
    assert len(wf) == 6 and all(r["sample_type"] == "IN_AND_OUT_OF_SAMPLE" for r in wf)

    # verbatim: stored pooled OOS result equals the artifact's, to the digit
    art = json.loads(
        (EVIDENCE / "walk-forward-regime_grid-strict_risk_admissible.json").read_text("utf-8")
    )
    stored = next(
        r for r in wf if r["strategy_id"] == "regime_grid" and "strict" in r["config"]["variant"]
    )
    assert stored["result"]["pooled"] == art["report"]["pooled"]
    assert stored["result"]["pooled"]["oos_net_pnl_sum"] == "-35.36991093"
    h = json.loads((EVIDENCE / "final-holdout.json").read_text("utf-8"))["holdout"]["regime_grid"]
    held = by[("HOLDOUT", "regime_grid")]["result"]["holdout_summary"]
    assert held["result_checksum"] == h["holdout_summary"]["result_checksum"]
    assert held["net_pnl"] == h["holdout_summary"]["net_pnl"]


def test_repeated_import_and_retry_create_no_duplicates(db):
    first = ri.import_studies(db, EVIDENCE, "tester")
    db.commit()
    again = ri.import_studies(db, EVIDENCE, "someone-else")
    db.commit()
    third = ri.import_studies(db, EVIDENCE, "tester")
    db.commit()
    assert len(first.inserted) == EXPECTED
    assert again.inserted == [] and len(again.skipped_duplicate) == EXPECTED
    assert third.inserted == [] and count(db) == EXPECTED


def test_changed_artifact_is_a_new_study_not_an_overwrite(db, tmp_path):
    d = tmp_path / "copy"
    shutil.copytree(EVIDENCE, d, ignore=IGN)
    ri.import_studies(db, d, "t")
    db.commit()
    p = d / "final-holdout.json"
    doc = json.loads(p.read_text("utf-8"))
    doc["holdout"]["regime_grid"]["holdout_summary"]["net_pnl"] = "999"
    p.write_text(json.dumps(doc), encoding="utf-8")
    rep = ri.import_studies(db, d, "t")
    db.commit()
    assert rep.inserted == ["Final holdout: regime_grid"]  # new content -> new row, old untouched
    assert count(db) == EXPECTED + 1


def test_malformed_input_inserts_nothing(db, tmp_path):
    d = tmp_path / "bad"
    shutil.copytree(EVIDENCE, d, ignore=IGN)
    (d / "sensitivity.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ri.StudyImportError):
        ri.import_studies(db, d, "t")
    db.rollback()
    assert count(db) == 0
    (d / "data-report.json").unlink()
    with pytest.raises(ri.StudyImportError, match="data-report"):
        ri.import_studies(db, d, "t")
    with pytest.raises(ri.StudyImportError):
        ri.import_studies(db, tmp_path / "nope", "t")


def test_stored_studies_are_append_only(db):
    ri.import_studies(db, EVIDENCE, "t")
    db.commit()
    for sql in (
        "update research_validations set title='x'",
        "delete from research_validations",
        "truncate research_validations",
    ):
        with pytest.raises(Exception):  # noqa: B017  (any DB-level refusal is the point)
            db.execute(text(sql))
        db.rollback()
    assert count(db) == EXPECTED


def test_list_and_detail_expose_sample_type_and_provenance(db):
    ri.import_studies(db, EVIDENCE, "t")
    db.commit()
    rows = rs.list_validations(db)
    assert len(rows) == EXPECTED and all(r["sample_type"] != "UNSPECIFIED" for r in rows)
    from asta.db.research_models import ResearchValidation

    v = db.query(ResearchValidation).filter_by(kind="HOLDOUT", strategy_id="trend_pullback").one()
    full = rs.validation_json(v)
    assert full["sample_type"] == "FINAL_HOLDOUT" and full["strategy_version"]
    assert full["source_datasets"]["BTC-USDT"]["checksum"]
    assert full["result"]["holdout_summary"]["risk_verdict"] == "BREACHED"  # negative finding kept


# ------------------------------------------------------------------ API
def _studies_dir(client, name="run1"):
    d = client.imp / "studies" / name
    shutil.copytree(EVIDENCE, d, ignore=IGN)
    return d


def test_api_import_rbac_csrf_idempotency_and_dedupe(client, db):
    _studies_dir(client)
    sign_in(client, db, "viewer1", "viewer")
    assert post(client, "/v1/validations/import", {"directory": "run1"}).status_code == 403
    client.cookies.clear()
    sign_in(client, db, "op1", "operator")
    nocsrf = post(client, "/v1/validations/import", {"directory": "run1"}, token=False)
    assert nocsrf.status_code in (401, 403)
    r = post(client, "/v1/validations/import", {"directory": "run1"}, key="same-key")
    assert r.status_code == 201 and len(r.json()["inserted"]) == EXPECTED
    again = post(client, "/v1/validations/import", {"directory": "run1"}, key="same-key")
    assert again.headers.get("Idempotent-Replay") == "true"
    other = post(client, "/v1/validations/import", {"directory": "run1"})
    assert other.status_code == 201
    assert other.json()["inserted"] == [] and len(other.json()["skipped_duplicate"]) == EXPECTED
    lst = client.get("/v1/validations").json()["validations"]
    assert len(lst) == EXPECTED
    assert {v["sample_type"] for v in lst} == {"IN_SAMPLE", "IN_AND_OUT_OF_SAMPLE", "FINAL_HOLDOUT"}
    s = client.ctx.sessions()
    try:
        assert any(a.action == "research.validations.import" for a in recent_audit(s, 20))
    finally:
        s.close()


def test_api_import_rejects_traversal_and_bad_input(client, db):
    sign_in(client, db, "op1", "operator")
    for bad in ("..", "../x", "a/b", "a\\b", "", ".hidden", "x" * 80):
        assert post(client, "/v1/validations/import", {"directory": bad}).status_code in (400, 422)
    assert post(client, "/v1/validations/import", {"directory": "missing"}).status_code == 422
    d = client.imp / "studies" / "broken"
    d.mkdir(parents=True)
    (d / "data-report.json").write_text("[]", encoding="utf-8")
    assert post(client, "/v1/validations/import", {"directory": "broken"}).status_code == 422
    assert client.get("/v1/validations").json()["validations"] == []
