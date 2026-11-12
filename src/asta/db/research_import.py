"""Persist completed research studies (walk-forward, sensitivity, final holdout, ...) from the
artifact files written by ``scripts/run_research.py``.

Rules:
* The artifact content is stored verbatim (canonical JSON); nothing is recomputed or edited.
* Every row carries provenance: source dataset checksums (from ``data-report.json``), strategy
  version (from the registry), the artifact's SHA-256, the generation timestamp and a sample type.
* A deterministic content hash with a unique index makes repeated imports and retries no-ops.
* All-or-nothing per call: malformed input raises ``StudyImportError`` before anything is inserted.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from asta.db.research_models import ResearchValidation
from asta.research.runner import canonical
from asta.research.strategies.registry import REGISTRY

IN_SAMPLE = "IN_SAMPLE"
IN_AND_OUT_OF_SAMPLE = "IN_AND_OUT_OF_SAMPLE"
FINAL_HOLDOUT = "FINAL_HOLDOUT"
SAMPLE_TYPES = (IN_SAMPLE, IN_AND_OUT_OF_SAMPLE, FINAL_HOLDOUT)


class StudyImportError(ValueError):
    pass


@dataclass
class ImportReport:
    inserted: list[str] = field(default_factory=list)
    skipped_duplicate: list[str] = field(default_factory=list)
    missing_files: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "inserted": self.inserted,
            "skipped_duplicate": self.skipped_duplicate,
            "missing_files": self.missing_files,
        }


@dataclass
class _Study:
    kind: str
    strategy_id: str
    title: str
    sample_type: str
    config: dict[str, Any]
    result: dict[str, Any]
    advisory: dict[str, Any]
    cycle: int
    oos_reused: bool
    artifact: str


def _load(path: Path) -> tuple[Any, str]:
    raw = path.read_bytes()
    try:
        return json.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise StudyImportError(f"{path.name}: not valid JSON ({e})") from e


def _need(d: Any, key: str, where: str) -> Any:
    if not isinstance(d, dict) or key not in d:
        raise StudyImportError(f"{where}: missing '{key}'")
    return d[key]


def _version(strategy_id: str) -> str | None:
    info = REGISTRY.get(strategy_id)
    return info.version if info else None


def content_hash(s: _Study) -> str:
    body = {
        "kind": s.kind,
        "strategy_id": s.strategy_id,
        "title": s.title,
        "sample_type": s.sample_type,
        "config": s.config,
        "result": s.result,
    }
    return hashlib.sha256(canonical(body).encode()).hexdigest()


def _datasets(report: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    ds = _need(report, "datasets", "data-report.json")
    out = {
        sym: {
            "checksum": d.get("checksum"),
            "timeframe": d.get("timeframe"),
            "period": d.get("period"),
            "rows": d.get("rows"),
            "source_type": d.get("source_type"),
            "root_source_type": (d.get("provenance") or {}).get("root_source_type"),
            "parents": d.get("parents"),
            "validation": d.get("validation"),
        }
        for sym, d in ds.items()
    }
    grade = all(bool(d.get("accepted_for_research")) for d in ds.values())
    return out, grade


def _collect(
    directory: Path, report: ImportReport
) -> tuple[list[_Study], dict[str, Any], bool, datetime | None, dict[str, str]]:
    data_report, data_sha = (
        _load(directory / "data-report.json")
        if (directory / "data-report.json").exists()
        else (None, "")
    )
    if data_report is None:
        raise StudyImportError("data-report.json is required (source dataset provenance)")
    datasets, research_grade = _datasets(data_report)
    generated: datetime | None = None
    meta_path = directory / "run-meta.json"
    if meta_path.exists():
        meta, _ = _load(meta_path)
        g = _need(meta, "generated_at", "run-meta.json")
        generated = datetime.fromisoformat(g)
    shas: dict[str, str] = {"data-report.json": data_sha}
    studies: list[_Study] = []

    for path in sorted(directory.glob("walk-forward-*.json")):
        doc, sha = _load(path)
        shas[path.name] = sha
        rep = _need(doc, "report", path.name)
        sid = _need(rep, "strategy_id", path.name)
        variant = path.stem.removeprefix(f"walk-forward-{sid}-")
        studies.append(
            _Study(
                kind="WALK_FORWARD",
                strategy_id=sid,
                title=f"Walk-forward: {sid} [{variant}]",
                sample_type=IN_AND_OUT_OF_SAMPLE,
                config={
                    "variant": variant,
                    "grid_size": rep.get("grid_size"),
                    "windows": [
                        {"index": w.get("index"), "train": w.get("train"), "test": w.get("test")}
                        for w in rep.get("windows", [])
                    ],
                },
                result={**rep, "in_vs_out_of_sample": doc.get("in_vs_out_of_sample")},
                advisory=doc.get("advisory") or {},
                cycle=int(rep.get("cycle", 1)),
                oos_reused=bool(rep.get("oos_reused", False)),
                artifact=path.name,
            )
        )

    if (directory / "sensitivity.json").exists():
        doc, sha = _load(directory / "sensitivity.json")
        shas["sensitivity.json"] = sha
        for sid, s in sorted(doc.items()):
            flags = list(_need(s, "instability_flags", f"sensitivity.json/{sid}"))
            studies.append(
                _Study(
                    kind="SENSITIVITY",
                    strategy_id=sid,
                    title=f"Sensitivity: {sid} (full period, default parameters)",
                    sample_type=IN_SAMPLE,
                    config={
                        "note": "full 3-year period; parameters were not selected out-of-sample"
                    },
                    result={
                        "scenarios": list(s.get("parameters", [])) + list(s.get("scenarios", [])),
                        "instability_flags": flags,
                        "unstable": s.get("unstable"),
                        "warnings": flags,
                    },
                    advisory={},
                    cycle=1,
                    oos_reused=False,
                    artifact="sensitivity.json",
                )
            )

    if (directory / "quarterly-windows.json").exists():
        doc, sha = _load(directory / "quarterly-windows.json")
        shas["quarterly-windows.json"] = sha
        for sid, rows in sorted(doc.items()):
            studies.append(
                _Study(
                    kind="WINDOWS",
                    strategy_id=sid,
                    title=f"Fixed-parameter 3-month windows: {sid}",
                    sample_type=IN_SAMPLE,
                    config={"note": "default parameters evaluated on consecutive windows"},
                    result={"scenarios": rows, "warnings": []},
                    advisory={},
                    cycle=1,
                    oos_reused=False,
                    artifact="quarterly-windows.json",
                )
            )

    if (directory / "final-holdout.json").exists():
        doc, sha = _load(directory / "final-holdout.json")
        shas["final-holdout.json"] = sha
        hold = _need(doc, "holdout", "final-holdout.json")
        registry = doc.get("registry", [])
        for sid, h in sorted(hold.items()):
            reg = [r for r in registry if str(r.get("key", "")).startswith(f"{sid}:")]
            studies.append(
                _Study(
                    kind="HOLDOUT",
                    strategy_id=sid,
                    title=f"Final holdout: {sid}",
                    sample_type=FINAL_HOLDOUT,
                    config={
                        "selection_window": h.get("selection_window"),
                        "holdout_window": h.get("holdout_window"),
                        "selected_params": h.get("selected_params"),
                        "registry": reg,
                    },
                    result={
                        **h,
                        "warnings": [
                            "Final holdout evaluated once; the window is consumed. Any reuse "
                            "requires a new validation cycle on fresh data."
                        ],
                    },
                    advisory={},
                    cycle=int(reg[0]["cycle"]) if reg else 1,
                    oos_reused=False,
                    artifact="final-holdout.json",
                )
            )

    if (directory / "baseline-comparison.json").exists():
        doc, sha = _load(directory / "baseline-comparison.json")
        shas["baseline-comparison.json"] = sha
        studies.append(
            _Study(
                kind="BASELINE_COMPARISON",
                strategy_id="ALL",
                title="Full-period strategies vs passive-hold and all-cash baselines",
                sample_type=IN_SAMPLE,
                config={"note": "identical data, costs and risk budget for every row"},
                result={**doc, "warnings": []},
                advisory={},
                cycle=1,
                oos_reused=False,
                artifact="baseline-comparison.json",
            )
        )
    for expected in ("walk-forward-*.json",):
        if not list(directory.glob(expected)):
            report.missing_files.append(expected)
    for name in (
        "sensitivity.json",
        "final-holdout.json",
        "baseline-comparison.json",
        "quarterly-windows.json",
    ):
        if not (directory / name).exists():
            report.missing_files.append(name)
    return studies, datasets, research_grade, generated, shas


def import_studies(db: Session, directory: Path, user: str) -> ImportReport:
    """Insert every study found in ``directory``; already-stored studies are skipped."""
    if not directory.is_dir():
        raise StudyImportError("study directory not found")
    report = ImportReport()
    studies, datasets, grade, generated, shas = _collect(directory, report)
    for s in studies:
        h = content_hash(s)
        config = {
            **s.config,
            "source_artifact_sha256": shas.get(s.artifact),
            "source_artifact": s.artifact,
        }
        stmt = (
            pg_insert(ResearchValidation)
            .values(
                kind=s.kind,
                strategy_id=s.strategy_id,
                title=s.title,
                config=json.loads(canonical(config)),
                result=json.loads(canonical(s.result)),
                cycle=s.cycle,
                oos_reused=s.oos_reused,
                research_grade=grade,
                advisory=json.loads(canonical(s.advisory)),
                created_by=user,
                content_hash=h,
                sample_type=s.sample_type,
                strategy_version=_version(s.strategy_id),
                source_datasets=json.loads(canonical(datasets)),
                source_artifact=f"{s.artifact}@{(shas.get(s.artifact) or '')[:16]}",
                generated_at=generated,
            )
            .on_conflict_do_nothing(index_elements=["content_hash"])
            .returning(ResearchValidation.id)
        )
        row = db.execute(stmt).first()
        (report.inserted if row else report.skipped_duplicate).append(s.title)
    return report


def count_studies(db: Session) -> int:
    return len(db.execute(select(ResearchValidation.id)).all())
