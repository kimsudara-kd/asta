"""Offline research API (M1). These are INTERNAL research endpoints over local files and simulated
results: there is no exchange connectivity, no order submission and no live path here.

Every state-changing route needs: authentication (MFA-complete), an RBAC permission, the CSRF
header, an `Idempotency-Key` header, and writes an audit event."""

import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from asta.api.deps import Db, require, require_write
from asta.auth import service as auth
from asta.auth.rbac import Permission
from asta.core.environment import Environment
from asta.db import research_import as ri
from asta.db import research_store as rs
from asta.db.audit_store import append_audit
from asta.db.research_models import BacktestRun, HistoricalDataset, HistoricalDatasetVersion
from asta.research.data.importer import ImportRejected, import_candles_file
from asta.research.data.profiles import (
    DEFAULT_FEES,
    FeeSchedule,
    RuleProfile,
    fixture_rules,
    okx_assumed_from,
)
from asta.research.data.quality import validate_bars
from asta.research.data.resample import ResampleError, resample
from asta.research.data.store import DatasetFileStore, StoreError
from asta.research.runner import BacktestConfig
from asta.research.sim.execution import ExecutionModel
from asta.research.sim.risk import SimRiskLimits
from asta.research.strategies.registry import REGISTRY, build_strategy, default_params_json
from asta.research.types import Timeframe
from asta.research_jobs import QueueFull, ResearchWorker, cancel_queued, current_git_commit

FILE_RE = re.compile(r"^[A-Za-z0-9._-]{1,150}\.csv$")
KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
LABELS = ["SIMULATED", "HISTORICAL DATA", "ESTIMATED FILLS", "NOT LIVE TRADING"]


_view = require(Permission.VIEW_RESEARCH)
_can_import = require_write(Permission.IMPORT_DATA)
_can_run = require_write(Permission.RUN_RESEARCH)
User = Annotated[auth.Principal, Depends(_view)]
Importer = Annotated[auth.Principal, Depends(_can_import)]
Runner = Annotated[auth.Principal, Depends(_can_run)]


@dataclass
class ResearchRuntime:
    store: DatasetFileStore
    import_dir: Path
    worker: ResearchWorker | None = None


class ImportBody(BaseModel):
    file_name: str = Field(pattern=FILE_RE.pattern)
    tick_size: Decimal | None = Field(default=None, gt=0, le=1000)


class StudyImportBody(BaseModel):
    directory: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class DeriveBody(BaseModel):
    timeframe: Literal["15m", "1h", "4h", "1d", "5m"]


class RiskBody(BaseModel):
    profile: Literal["default", "custom", "unconstrained_reference"] = "default"
    max_asset_pct: Decimal | None = Field(default=None, gt=0, le=1)
    max_total_pct: Decimal | None = Field(default=None, gt=0, le=1)
    daily_loss_pct: Decimal | None = Field(default=None, gt=0, le=1)
    max_drawdown_pct: Decimal | None = Field(default=None, gt=0, le=1)


class BacktestBody(BaseModel):
    strategy_id: str
    strategy_params: dict[str, Any] = Field(default_factory=dict)
    dataset_version_ids: dict[str, uuid.UUID]
    rule_set: str
    fee_profile: str
    fee_multiplier: Decimal = Field(default=Decimal(1), ge=0, le=10)
    slippage_multiplier: Decimal = Field(default=Decimal(1), ge=0, le=10)
    latency_bars: int = Field(default=0, ge=0, le=5)
    starting_cash: Decimal = Field(default=Decimal(1000), ge=100, le=1_000_000)
    risk: RiskBody = Field(default_factory=RiskBody)
    period_start: datetime | None = None
    period_end: datetime | None = None
    warmup_bars: int = Field(default=0, ge=0, le=5000)
    label: str = Field(default="", max_length=120)
    persist_detail: bool = True
    seed: int = Field(default=0, ge=0, le=2**31 - 1)

    @field_validator("period_start", "period_end")
    @classmethod
    def _utc(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        if v.tzinfo is None:
            raise ValueError("timestamps must include a UTC offset")
        return v.astimezone(UTC)


def _rule_sets(import_dir: Path) -> dict[str, tuple[RuleProfile, ...]]:
    sets: dict[str, tuple[RuleProfile, ...]] = {}
    for f in sorted(import_dir.glob("rules_binance_*.json")):
        try:
            rules = tuple(
                RuleProfile.from_json(p) for p in json.loads(f.read_text(encoding="utf-8"))
            )
        except (ValueError, KeyError, OSError):
            continue
        sets[f"binance-{f.stem.removeprefix('rules_binance_')}"] = rules
        sets[f"okx-assumed-{f.stem.removeprefix('rules_binance_')}"] = okx_assumed_from(rules)
    sets["fixture-generic"] = fixture_rules(
        ("BTC-USDT", "ETH-USDT", "SOL-USDT"), datetime(2024, 1, 1, tzinfo=UTC)
    )
    return sets


def _fee_profiles() -> dict[str, FeeSchedule]:
    return {f.schedule_id: f for f in DEFAULT_FEES.values()}


def register_research_routes(app: FastAPI, ctx: Any) -> None:
    runtime: ResearchRuntime = ctx.research

    def idem_key(value: str | None) -> str:
        if not value or not KEY_RE.match(value):
            raise HTTPException(400, "Idempotency-Key header (8-128 chars) is required")
        return value

    def replay(db: Db, key: str, user: str, endpoint: str, body: Any) -> JSONResponse | str:
        rhash = rs.request_hash(body)
        try:
            hit = rs.idempotent_lookup(db, key, user, endpoint, rhash)
        except rs.IdempotencyConflict as exc:
            raise HTTPException(409, str(exc)) from None
        if hit is not None:
            return JSONResponse(hit[1], status_code=hit[0], headers={"Idempotent-Replay": "true"})
        return rhash

    def version_json(db: Db, vid: uuid.UUID) -> dict[str, Any]:
        v = rs.get_version(db, vid)
        if v is None:
            raise HTTPException(404, "dataset version not found")
        head = db.get(HistoricalDataset, v.dataset_id)
        assert head is not None
        return rs.dataset_row_json(v, head)

    # ------------------------------------------------------------------ datasets
    @app.get("/v1/datasets")
    def datasets(db: Db, _: User) -> dict[str, Any]:
        return {"labels": LABELS, "datasets": rs.list_datasets(db)}

    @app.get("/v1/datasets/importable")
    def importable(_: User) -> dict[str, Any]:
        out = []
        for f in sorted(runtime.import_dir.glob("*.csv")):
            side = f.with_name(f.name + ".meta.json")
            if not side.is_file():
                continue
            try:
                m = json.loads(side.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            out.append(
                {
                    "file_name": f.name,
                    "size_bytes": f.stat().st_size,
                    "symbol": m.get("symbol"),
                    "timeframe": m.get("timeframe"),
                    "source_type": m.get("source_type"),
                    "source": m.get("source"),
                }
            )
        return {
            "files": out,
            "note": "LOCAL files only; the importer never opens a network connection",
        }

    @app.get("/v1/datasets/{version_id}/quality")
    def quality(version_id: uuid.UUID, db: Db, _: User) -> dict[str, Any]:
        return {
            "dataset": version_json(db, version_id),
            "reports": rs.quality_reports(db, version_id),
        }

    @app.post("/v1/datasets/import", status_code=201)
    def import_dataset(
        body: ImportBody,
        request: Request,
        db: Db,
        p: Importer,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> Any:
        key = idem_key(idempotency_key)
        hit = replay(db, key, p.user_id, "POST /v1/datasets/import", body.model_dump(mode="json"))
        if isinstance(hit, JSONResponse):
            return hit
        root = runtime.import_dir.resolve()
        path = (root / body.file_name).resolve()
        if path.parent != root:  # a bare file name inside the import directory, nothing else
            raise HTTPException(400, "file must be a plain name inside the import directory")
        try:
            res = import_candles_file(path, body.tick_size)
        except ImportRejected as exc:
            raise HTTPException(422, f"cannot import: {exc}") from None
        runtime.store.save(res.dataset)  # immutable, content-addressed
        v = rs.register_dataset(db, res.dataset, res.report, p.username, body.file_name)
        append_audit(
            db,
            p.username,
            "research.dataset.import",
            {
                "file": body.file_name,
                "version": str(v.id),
                "status": res.report.status.value,
                "rows": res.dataset.meta.row_count,
            },
            Environment.BACKTEST,
        )
        out = {"dataset": version_json(db, v.id), "quality": res.report.to_json(), "labels": LABELS}
        rs.idempotent_store(db, key, p.user_id, "POST /v1/datasets/import", hit, 201, out)
        db.commit()
        return out

    @app.post("/v1/datasets/{version_id}/validate")
    def validate_dataset(
        version_id: uuid.UUID,
        db: Db,
        p: Importer,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> Any:
        key = idem_key(idempotency_key)
        hit = replay(db, key, p.user_id, f"POST /v1/datasets/{version_id}/validate", {})
        if isinstance(hit, JSONResponse):
            return hit
        v = rs.get_version(db, version_id)
        if v is None:
            raise HTTPException(404, "dataset version not found")
        try:
            ds = runtime.store.load(v.checksum)  # also re-verifies the checksum on disk
        except StoreError as exc:
            raise HTTPException(409, f"stored data failed verification: {exc}") from None
        head = db.get(HistoricalDataset, v.dataset_id)
        assert head is not None
        report = validate_bars(ds.bars, ds.meta.timeframe, head.venue, head.symbol)
        rs.add_quality_report(db, v.id, report, p.username)
        append_audit(
            db,
            p.username,
            "research.dataset.validate",
            {"version": str(v.id), "status": report.status.value},
            Environment.BACKTEST,
        )
        out = {
            "dataset": rs.dataset_row_json(v, head),
            "quality": report.to_json(),
            "labels": LABELS,
        }
        rs.idempotent_store(
            db, key, p.user_id, f"POST /v1/datasets/{version_id}/validate", hit, 200, out
        )
        db.commit()
        return out

    @app.post("/v1/datasets/{version_id}/derive", status_code=201)
    def derive_dataset(
        version_id: uuid.UUID,
        body: DeriveBody,
        db: Db,
        p: Importer,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> Any:
        key = idem_key(idempotency_key)
        ep = f"POST /v1/datasets/{version_id}/derive"
        hit = replay(db, key, p.user_id, ep, body.model_dump(mode="json"))
        if isinstance(hit, JSONResponse):
            return hit
        v = rs.get_version(db, version_id)
        if v is None:
            raise HTTPException(404, "dataset version not found")
        try:
            parent = runtime.store.load(v.checksum)
            res = resample(parent, Timeframe(body.timeframe))
        except (StoreError, ResampleError) as exc:
            raise HTTPException(422, str(exc)) from None
        runtime.store.save(res.dataset)
        nv = rs.register_dataset(db, res.dataset, res.report, p.username, f"derived:{v.id}")
        append_audit(
            db,
            p.username,
            "research.dataset.derive",
            {"parent": str(v.id), "version": str(nv.id), "timeframe": body.timeframe},
            Environment.BACKTEST,
        )
        out = {
            "dataset": version_json(db, nv.id),
            "quality": res.report.to_json(),
            "labels": LABELS,
        }
        rs.idempotent_store(db, key, p.user_id, ep, hit, 201, out)
        db.commit()
        return out

    # ------------------------------------------------------------------ strategies / profiles
    @app.get("/v1/strategies")
    def strategies(_: User) -> dict[str, Any]:
        return {
            "labels": LABELS,
            "strategies": [
                {
                    "strategy_id": i.strategy_id,
                    "version": i.version,
                    "display_name": i.display_name,
                    "kind": i.kind,
                    "description": i.description,
                    "default_params": default_params_json(i.strategy_id),
                }
                for i in REGISTRY.values()
            ],
        }

    @app.get("/v1/profiles")
    def profiles(_: User) -> dict[str, Any]:
        return {
            "rule_sets": [
                {
                    "id": k,
                    "venue": v[0].venue if v else None,
                    "symbols": [r.symbol for r in v],
                    "verified": all(r.verified for r in v),
                    "source": v[0].source if v else "",
                    "retrieved_at": v[0].retrieved_at.isoformat() if v else None,
                }
                for k, v in _rule_sets(runtime.import_dir).items()
            ],
            "fee_profiles": [f.to_json() for f in _fee_profiles().values()],
            "execution_model": ExecutionModel().to_json(),
            "risk_defaults": SimRiskLimits.default().to_json(),
            "note": "all exchange rules and fees are assumptions unless verified=true with evidence",
        }

    # ------------------------------------------------------------------ backtests
    def to_config(body: BacktestBody, db: Db) -> tuple[BacktestConfig, dict[str, str]]:
        if body.strategy_id not in REGISTRY:
            raise HTTPException(422, f"unknown strategy {body.strategy_id!r}")
        try:
            build_strategy(body.strategy_id, body.strategy_params)  # fail early on bad parameters
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(422, f"invalid strategy parameters: {exc}") from None
        checks: dict[str, str] = {}
        tfs: set[str] = set()
        for sym, vid in body.dataset_version_ids.items():
            v = db.get(HistoricalDatasetVersion, vid)
            head = db.get(HistoricalDataset, v.dataset_id) if v else None
            if v is None or head is None:
                raise HTTPException(422, f"dataset version for {sym} not found")
            if head.symbol != sym:
                raise HTTPException(422, f"dataset version belongs to {head.symbol}, not {sym}")
            if v.validation_status == "REJECTED":
                raise HTTPException(
                    422, f"dataset for {sym} is REJECTED and cannot be used for results"
                )
            tfs.add(head.timeframe)
            checks[sym] = v.checksum
        if len(tfs) != 1:
            raise HTTPException(422, "all datasets must share one timeframe")
        sets = _rule_sets(runtime.import_dir)
        rule_set = sets.get(body.rule_set)
        if rule_set is None:
            raise HTTPException(422, "unknown rule_set")
        by_sym = {r.symbol: r for r in rule_set}
        missing = [s for s in checks if s not in by_sym]
        if missing:
            raise HTTPException(422, f"rule set has no profile for {missing}")
        fees = _fee_profiles().get(body.fee_profile)
        if fees is None:
            raise HTTPException(422, "unknown fee_profile")
        if body.fee_multiplier != 1:
            fees = fees.scaled(body.fee_multiplier, f"x{body.fee_multiplier}")
        execution = ExecutionModel()
        if body.slippage_multiplier != 1:
            execution = execution.scaled_slippage(body.slippage_multiplier)
        rk = body.risk
        if rk.profile == "default":
            risk = SimRiskLimits.default(body.starting_cash)
        elif rk.profile == "unconstrained_reference":
            risk = SimRiskLimits.unconstrained_reference(body.starting_cash)
        else:
            d = SimRiskLimits.default(body.starting_cash)
            risk = SimRiskLimits(
                body.starting_cash,
                rk.max_asset_pct or d.max_asset_pct,
                rk.max_total_pct or d.max_total_pct,
                rk.daily_loss_pct,
                rk.max_drawdown_pct,
                "RISK_CUSTOM",
            )
        if body.period_start and body.period_end and body.period_start >= body.period_end:
            raise HTTPException(422, "period_start must be before period_end")
        cfg = BacktestConfig(
            strategy_id=body.strategy_id,
            strategy_params=body.strategy_params,
            dataset_checksums=checks,
            timeframe=Timeframe(next(iter(tfs))),
            rules=tuple(by_sym[s] for s in sorted(checks)),
            fees=fees,
            execution=execution,
            risk=risk,
            starting_cash=body.starting_cash,
            latency_bars=body.latency_bars,
            period_start=body.period_start,
            period_end=body.period_end,
            warmup_bars=body.warmup_bars,
            seed=body.seed,
            label=body.label or body.strategy_id,
        )
        return cfg, {s: str(v) for s, v in body.dataset_version_ids.items()}

    @app.get("/v1/backtests")
    def backtests(db: Db, _: User, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        return {"labels": LABELS, "runs": rs.list_runs(db, max(1, min(limit, 200)), max(0, offset))}

    def get_run(db: Db, run_id: uuid.UUID) -> BacktestRun:
        r = db.get(BacktestRun, run_id)
        if r is None:
            raise HTTPException(404, "backtest run not found")
        return r

    @app.get("/v1/backtests/{run_id}")
    def backtest(run_id: uuid.UUID, db: Db, _: User) -> dict[str, Any]:
        return rs.run_json(db, get_run(db, run_id))

    @app.get("/v1/backtests/{run_id}/results")
    def results(run_id: uuid.UUID, db: Db, _: User) -> dict[str, Any]:
        r = get_run(db, run_id)
        perf = rs.performance_json(db, run_id)
        return {
            "run": rs.run_json(db, r),
            "performance": perf,
            "labels": LABELS,
            "honesty": "SIMULATED marked-to-market equity with ESTIMATED fills; not realised cash profit",
        }

    @app.get("/v1/backtests/{run_id}/equity")
    def equity(run_id: uuid.UUID, db: Db, _: User, max_points: int = 2000) -> dict[str, Any]:
        get_run(db, run_id)
        return {
            "labels": LABELS,
            "points": rs.equity_points(db, run_id, max(10, min(max_points, 20000))),
        }

    @app.get("/v1/backtests/{run_id}/trades")
    def sim_fills(
        run_id: uuid.UUID, db: Db, _: User, limit: int = 500, offset: int = 0
    ) -> dict[str, Any]:
        get_run(db, run_id)
        return rs.fills_json(db, run_id, max(1, min(limit, 5000)), max(0, offset))

    @app.get("/v1/backtests/{run_id}/risk")
    def risk_report(run_id: uuid.UUID, db: Db, _: User) -> dict[str, Any]:
        get_run(db, run_id)
        return rs.risk_json(db, run_id)

    @app.post("/v1/backtests", status_code=202)
    def create_backtest(
        body: BacktestBody,
        db: Db,
        p: Runner,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> Any:
        key = idem_key(idempotency_key)
        hit = replay(db, key, p.user_id, "POST /v1/backtests", body.model_dump(mode="json"))
        if isinstance(hit, JSONResponse):
            return hit
        if runtime.worker is None:
            raise HTTPException(503, "research worker is not running")
        cfg, version_ids = to_config(body, db)
        run = rs.create_run(
            db, cfg, version_ids, p.username, current_git_commit(), body.persist_detail
        )
        append_audit(
            db,
            p.username,
            "research.backtest.create",
            {"run": str(run.id), "strategy": cfg.strategy_id, "config_hash": cfg.config_hash()},
            Environment.BACKTEST,
        )
        db.commit()
        try:
            runtime.worker.submit(run.id, cfg)
        except QueueFull:
            cancel_queued(db, run.id)
            raise HTTPException(429, "too many queued backtests; try again shortly") from None
        out = {
            "id": str(run.id),
            "status": "QUEUED",
            "config_hash": cfg.config_hash(),
            "labels": LABELS,
        }
        rs.idempotent_store(db, key, p.user_id, "POST /v1/backtests", hit, 202, out)
        db.commit()
        return out

    @app.post("/v1/backtests/{run_id}/cancel")
    def cancel_backtest(
        run_id: uuid.UUID,
        db: Db,
        p: Runner,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> Any:
        key = idem_key(idempotency_key)
        ep = f"POST /v1/backtests/{run_id}/cancel"
        hit = replay(db, key, p.user_id, ep, {})
        if isinstance(hit, JSONResponse):
            return hit
        r = get_run(db, run_id)
        if r.status in ("COMPLETED", "FAILED", "CANCELLED"):
            raise HTTPException(409, f"run is already {r.status}")
        if cancel_queued(db, run_id):
            state = "CANCELLED"
        else:
            assert runtime.worker is not None
            runtime.worker.request_cancel(run_id)
            state = "CANCELLING"
        append_audit(
            db,
            p.username,
            "research.backtest.cancel",
            {"run": str(run_id), "state": state},
            Environment.BACKTEST,
        )
        out = {"id": str(run_id), "state": state}
        rs.idempotent_store(db, key, p.user_id, ep, hit, 200, out)
        db.commit()
        return out

    # ------------------------------------------------------------------ validations (stored research)
    @app.get("/v1/validations")
    def validations(db: Db, _: User) -> dict[str, Any]:
        return {"labels": LABELS, "validations": rs.list_validations(db)}

    @app.post("/v1/validations/import", status_code=201)
    def import_validations(
        body: StudyImportBody,
        db: Db,
        p: Importer,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> Any:
        """Persist completed study artifacts from <import_dir>/studies/<directory> (local files
        only; verbatim, de-duplicated by content hash, so a retry or repeat is a no-op)."""
        key = idem_key(idempotency_key)
        ep = "POST /v1/validations/import"
        hit = replay(db, key, p.user_id, ep, body.model_dump(mode="json"))
        if isinstance(hit, JSONResponse):
            return hit
        root = (runtime.import_dir / "studies").resolve()
        path = (root / body.directory).resolve()
        if path.parent != root:
            raise HTTPException(400, "directory must be a plain name under the studies directory")
        try:
            rep = ri.import_studies(db, path, p.username)
        except ri.StudyImportError as exc:
            db.rollback()
            raise HTTPException(422, f"cannot import studies: {exc}") from None
        append_audit(
            db,
            p.username,
            "research.validations.import",
            {"directory": body.directory, **{k: len(v) for k, v in rep.to_json().items()}},
            Environment.BACKTEST,
        )
        out = {"labels": LABELS, **rep.to_json()}
        rs.idempotent_store(db, key, p.user_id, ep, hit, 201, out)
        db.commit()
        return out

    @app.get("/v1/validations/{vid}")
    def validation(vid: uuid.UUID, db: Db, _: User) -> dict[str, Any]:
        from asta.db.research_models import ResearchValidation

        row = db.execute(
            select(ResearchValidation).where(ResearchValidation.id == vid)
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, "validation not found")
        return rs.validation_json(row)
