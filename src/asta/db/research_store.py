"""Persistence for the M1 research module. Results are written once (insert-only tables) and a run
becomes immutable when it reaches a terminal status (DB trigger)."""

from __future__ import annotations

import hashlib
import inspect
import json
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from asta.core.timeutil import utcnow
from asta.db.research_models import (
    BacktestRun,
    BacktestRunConfig,
    DatasetQualityReport,
    HistoricalDataset,
    HistoricalDatasetVersion,
    IdempotencyKey,
    PerformanceReportRow,
    PortfolioEquitySnapshot,
    ResearchHoldoutWindow,
    ResearchValidation,
    RiskEventRow,
    SimulatedFill,
    SimulatedLedgerEntry,
    SimulatedOrder,
    StrategyDefinition,
    StrategyVersion,
)
from asta.research.data.models import Dataset
from asta.research.data.quality import QualityReport
from asta.research.runner import BacktestConfig, canonical
from asta.research.runner import BacktestRun as RunOutput
from asta.research.strategies.registry import REGISTRY, default_params_json
from asta.research.validation.holdout import ConsumedWindow, HoldoutRegistry

TERMINAL = ("COMPLETED", "FAILED", "CANCELLED")


def _jsonify(obj: Any) -> Any:
    """Round-trip through the canonical encoder: Decimals become plain-notation strings."""
    return json.loads(canonical(obj))


# ------------------------------------------------------------------ strategies
def _fingerprint(cls: type) -> str:
    path = inspect.getsourcefile(cls)
    data = open(path, "rb").read() if path else cls.__qualname__.encode()
    return hashlib.sha256(data).hexdigest()


def sync_strategies(db: Session) -> dict[str, uuid.UUID]:
    """Make sure every registered strategy version exists (fingerprinted by its source)."""
    out: dict[str, uuid.UUID] = {}
    for sid, info in REGISTRY.items():
        d = db.execute(
            select(StrategyDefinition).where(StrategyDefinition.strategy_id == sid)
        ).scalar_one_or_none()
        if d is None:
            d = StrategyDefinition(
                strategy_id=sid,
                kind=info.kind,
                display_name=info.display_name,
                description=info.description,
            )
            db.add(d)
            db.flush()
        fp = _fingerprint(info.cls)
        v = db.execute(
            select(StrategyVersion).where(
                StrategyVersion.definition_id == d.id,
                StrategyVersion.version == info.version,
                StrategyVersion.code_fingerprint == fp,
            )
        ).scalar_one_or_none()
        if v is None:
            v = StrategyVersion(
                definition_id=d.id,
                version=info.version,
                code_fingerprint=fp,
                default_params=default_params_json(sid),
            )
            db.add(v)
            db.flush()
        out[sid] = v.id
    return out


# ------------------------------------------------------------------ datasets
def register_dataset(
    db: Session, ds: Dataset, report: QualityReport, user: str, file_name: str
) -> HistoricalDatasetVersion:
    """Idempotent by checksum: importing identical content returns the existing version."""
    existing = db.execute(
        select(HistoricalDatasetVersion).where(
            HistoricalDatasetVersion.checksum == ds.meta.checksum
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    m = ds.meta
    head = db.execute(
        select(HistoricalDataset).where(
            HistoricalDataset.venue == m.venue,
            HistoricalDataset.symbol == m.symbol,
            HistoricalDataset.timeframe == m.timeframe.value,
        )
    ).scalar_one_or_none()
    if head is None:
        head = HistoricalDataset(venue=m.venue, symbol=m.symbol, timeframe=m.timeframe.value)
        db.add(head)
        db.flush()
    v = HistoricalDatasetVersion(
        dataset_id=head.id,
        checksum=m.checksum,
        source=m.source,
        source_type=m.source_type.value,
        schema_version=m.schema_version,
        period_start=m.period_start,
        period_end=m.period_end,
        row_count=m.row_count,
        provenance=_jsonify(m.provenance),
        parent_checksums=list(m.parent_checksums),
        validation_status=(m.validation_status.value if m.validation_status else "REJECTED"),
        accepted_for_research=m.accepted_for_research,
        label=m.label,
        file_name=file_name,
        imported_at=m.imported_at,
        imported_by=user,
    )
    db.add(v)
    db.flush()
    add_quality_report(db, v.id, report, user)
    return v


def add_quality_report(
    db: Session, version_id: uuid.UUID, report: QualityReport, user: str
) -> DatasetQualityReport:
    row = DatasetQualityReport(
        version_id=version_id,
        status=report.status.value,
        report=_jsonify(report.to_json()),
        created_by=user,
    )
    db.add(row)
    db.flush()
    return row


def dataset_row_json(v: HistoricalDatasetVersion, head: HistoricalDataset) -> dict[str, Any]:
    return {
        "version_id": str(v.id),
        "dataset_id": str(head.id),
        "venue": head.venue,
        "symbol": head.symbol,
        "timeframe": head.timeframe,
        "checksum": v.checksum,
        "source": v.source,
        "source_type": v.source_type,
        "label": v.label,
        "period_start": v.period_start.isoformat(),
        "period_end": v.period_end.isoformat(),
        "row_count": v.row_count,
        "validation_status": v.validation_status,
        "accepted_for_research": v.accepted_for_research,
        "provenance": v.provenance,
        "parent_checksums": v.parent_checksums,
        "file_name": v.file_name,
        "imported_at": v.imported_at.isoformat(),
        "imported_by": v.imported_by,
        "schema_version": v.schema_version,
    }


def list_datasets(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(
        select(HistoricalDatasetVersion, HistoricalDataset)
        .join(HistoricalDataset, HistoricalDataset.id == HistoricalDatasetVersion.dataset_id)
        .order_by(
            HistoricalDataset.symbol,
            HistoricalDataset.timeframe,
            HistoricalDatasetVersion.imported_at.desc(),
        )
    ).all()
    return [dataset_row_json(v, h) for v, h in rows]


def get_version(db: Session, version_id: uuid.UUID) -> HistoricalDatasetVersion | None:
    return db.get(HistoricalDatasetVersion, version_id)


def quality_reports(db: Session, version_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = db.execute(
        select(DatasetQualityReport)
        .where(DatasetQualityReport.version_id == version_id)
        .order_by(DatasetQualityReport.created_at)
    ).scalars()
    return [
        {
            "id": str(r.id),
            "status": r.status,
            "report": r.report,
            "created_by": r.created_by,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


# ------------------------------------------------------------------ runs
def create_run(
    db: Session,
    config: BacktestConfig,
    version_ids: dict[str, str],
    user: str,
    git_commit: str,
    persist_detail: bool = True,
    corrects_run_id: uuid.UUID | None = None,
) -> BacktestRun:
    chash = config.config_hash()
    cfg_row = db.execute(
        select(BacktestRunConfig).where(BacktestRunConfig.config_hash == chash)
    ).scalar_one_or_none()
    if cfg_row is None:
        cfg_row = BacktestRunConfig(config_hash=chash, config=_jsonify(config.to_json()))
        db.add(cfg_row)
        db.flush()
    sv = sync_strategies(db)
    run = BacktestRun(
        config_id=cfg_row.id,
        strategy_version_id=sv.get(config.strategy_id),
        status="QUEUED",
        git_commit=git_commit,
        dataset_version_ids=version_ids,
        execution_model_version=config.execution.version,
        fee_schedule_version=config.fees.schedule_id,
        risk_policy_version=config.risk.version,
        seed=config.seed,
        label=config.label or f"{config.strategy_id}",
        persist_detail=persist_detail,
        progress_pct=0,
        notes=[],
        created_by=user,
        corrects_run_id=corrects_run_id,
    )
    db.add(run)
    db.flush()
    return run


def set_running(db: Session, run_id: uuid.UUID) -> None:
    db.execute(
        update(BacktestRun)
        .where(BacktestRun.id == run_id, BacktestRun.status == "QUEUED")
        .values(status="RUNNING", started_at=utcnow())
    )
    db.commit()


def set_progress(db: Session, run_id: uuid.UUID, pct: int) -> None:
    db.execute(
        update(BacktestRun)
        .where(BacktestRun.id == run_id, BacktestRun.status == "RUNNING")
        .values(progress_pct=max(0, min(99, pct)))
    )
    db.commit()


def finish_failed(db: Session, run_id: uuid.UUID, status: str, error: str) -> None:
    db.execute(
        update(BacktestRun)
        .where(BacktestRun.id == run_id, BacktestRun.status.in_(("QUEUED", "RUNNING")))
        .values(status=status, error=error[:2000], finished_at=utcnow())
    )
    db.commit()


def recover_interrupted(db: Session) -> int:
    """At start-up: runs left QUEUED/RUNNING by a previous process can never finish."""
    res = db.execute(
        update(BacktestRun)
        .where(BacktestRun.status.in_(("QUEUED", "RUNNING")))
        .values(
            status="FAILED", error="interrupted by process restart; resubmit", finished_at=utcnow()
        )
    )
    db.commit()
    return int(getattr(res, "rowcount", 0) or 0)


def _bulk_insert(db: Session, model: Any, rows: Sequence[dict[str, Any]]) -> None:
    """Multi-row INSERT ... VALUES batches. (The driver's executemany sends one round trip PER ROW,
    which made persisting a 20k-bar run take minutes.) Batches stay well below the 65535-parameter
    limit of one statement."""
    if not rows:
        return
    per_stmt = max(1, 30000 // max(1, len(rows[0])))
    for i in range(0, len(rows), per_stmt):
        db.execute(insert(model).values(list(rows[i : i + per_stmt])))


def save_result(db: Session, run_id: uuid.UUID, out: RunOutput) -> None:
    """Write every simulated artefact once, then flip the run to its terminal status."""
    row = db.get(BacktestRun, run_id)
    assert row is not None
    res = out.result
    perf = _jsonify(out.performance.to_json())
    db.add(PerformanceReportRow(run_id=run_id, metrics=perf, summary=_jsonify(_summary(out))))
    if row.persist_detail:
        orders = [
            {
                "run_id": run_id,
                "order_id": o.order_id,
                "symbol": o.symbol,
                "side": o.side.value,
                "type": o.type.value,
                "quantity": o.quantity,
                "limit_price": o.limit_price,
                "stop_price": o.stop_price,
                "status": o.status.value,
                "filled_qty": o.filled_qty,
                "tag": o.tag[:32],
                "client_id": o.client_id,
                "reject_reason": (o.reject_reason or "")[:48],
                "created_ts": o.created_ts,
                "events": _jsonify([[e.seq, e.ts, e.status.value, e.reason] for e in o.events]),
            }
            for o in res.orders
        ]
        fills = [
            {
                "run_id": run_id,
                "fill_id": f.fill_id,
                "order_id": f.order_id,
                "symbol": f.symbol,
                "side": f.side.value,
                "ts": f.ts,
                "price": f.price,
                "quantity": f.quantity,
                "liquidity": f.liquidity,
                "fee_asset": f.fee_asset,
                "fee_amount": f.fee_amount,
                "notional": f.notional,
                "reference_price": f.reference_price,
                "slippage_cost": f.slippage_cost,
                "realized_pnl": f.realized_pnl,
                "tag": f.tag[:32],
            }
            for f in res.fills
        ]
        ledger = [
            {
                "run_id": run_id,
                "seq": e.seq,
                "ts": e.ts,
                "kind": e.kind,
                "asset": e.asset,
                "amount": e.amount,
                "order_id": e.order_id,
                "fill_id": e.fill_id,
                "note": e.note[:120],
            }
            for e in res.ledger.entries
        ]
        equity = [
            {
                "run_id": run_id,
                "ts": p.ts,
                "equity": p.equity,
                "cash": p.cash,
                "positions_value": p.positions_value,
                "reserved_cash": p.reserved_cash,
                "exposure": p.exposure,
            }
            for p in res.equity
        ]
        for model, rows in (
            (SimulatedOrder, orders),
            (SimulatedFill, fills),
            (SimulatedLedgerEntry, ledger),
            (PortfolioEquitySnapshot, equity),
        ):
            _bulk_insert(db, model, rows)
    risk = [
        {
            "run_id": run_id,
            "seq": e.seq,
            "ts": e.ts,
            "kind": e.kind,
            "detail": e.detail,
            "equity": e.equity,
        }
        for e in res.risk_events
        if e.kind != "ORDER_REJECTED"
    ]
    _bulk_insert(db, RiskEventRow, risk)
    db.flush()
    db.execute(
        update(BacktestRun)
        .where(BacktestRun.id == run_id)
        .values(
            status="COMPLETED",
            result_status=res.status,
            risk_verdict=res.risk_verdict,
            research_grade=out.research_grade,
            result_checksum=out.result_checksum,
            progress_pct=100,
            notes=out.notes,
            finished_at=utcnow(),
        )
    )
    db.commit()


def _summary(out: RunOutput) -> dict[str, Any]:
    p = out.performance
    return {
        "net_pnl": p.returns.get("total_net_pnl"),
        "net_return_pct": p.returns.get("net_return_pct"),
        "ending_equity_marked": p.returns.get("ending_equity_marked"),
        "max_drawdown_pct": p.risk.get("max_drawdown_pct"),
        "round_trips": p.trading.get("closed_round_trips"),
        "fills": p.trading.get("fills"),
        "status": out.result.status,
        "risk_verdict": out.result.risk_verdict,
        "research_grade": out.research_grade,
    }


def run_json(db: Session, r: BacktestRun) -> dict[str, Any]:
    cfg = db.get(BacktestRunConfig, r.config_id)
    perf = db.execute(
        select(PerformanceReportRow.summary).where(PerformanceReportRow.run_id == r.id)
    ).scalar_one_or_none()
    return {
        "id": str(r.id),
        "label": r.label,
        "status": r.status,
        "progress_pct": r.progress_pct,
        "result_status": r.result_status,
        "risk_verdict": r.risk_verdict,
        "research_grade": r.research_grade,
        "strategy_id": cfg.config["strategy_id"] if cfg else None,
        "strategy_version": cfg.config.get("strategy_version") if cfg else None,
        "config_hash": cfg.config_hash if cfg else None,
        "config": cfg.config if cfg else None,
        "git_commit": r.git_commit,
        "dataset_version_ids": r.dataset_version_ids,
        "execution_model_version": r.execution_model_version,
        "fee_schedule_version": r.fee_schedule_version,
        "risk_policy_version": r.risk_policy_version,
        "seed": r.seed,
        "result_checksum": r.result_checksum,
        "error": r.error,
        "notes": r.notes,
        "created_by": r.created_by,
        "corrects_run_id": str(r.corrects_run_id) if r.corrects_run_id else None,
        "queued_at": r.queued_at.isoformat(),
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "summary": perf,
        "labels": ["SIMULATED", "HISTORICAL DATA", "ESTIMATED FILLS", "NOT LIVE TRADING"],
    }


def list_runs(db: Session, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    rows = (
        db.execute(
            select(BacktestRun).order_by(BacktestRun.queued_at.desc()).limit(limit).offset(offset)
        )
        .scalars()
        .all()
    )
    return [run_json(db, r) for r in rows]


def equity_points(db: Session, run_id: uuid.UUID, max_points: int = 2000) -> list[dict[str, Any]]:
    """Equity curve for charts. The curve is downsampled for transport only; drawdown is computed
    over the FULL series first so a chart can never understate it."""
    rows = (
        db.execute(
            select(PortfolioEquitySnapshot)
            .where(PortfolioEquitySnapshot.run_id == run_id)
            .order_by(PortfolioEquitySnapshot.id)
        )
        .scalars()
        .all()
    )
    if not rows:
        return []
    peak = rows[0].equity
    dd: list[str] = []
    for r in rows:
        peak = max(peak, r.equity)
        dd.append(format((peak - r.equity) / peak if peak else 0, "f"))
    stride = max(1, -(-len(rows) // max_points))
    idx = list(range(0, len(rows), stride))
    if idx[-1] != len(rows) - 1:
        idx.append(len(rows) - 1)
    return [
        {
            "ts": rows[i].ts.isoformat(),
            "equity": format(rows[i].equity, "f"),
            "cash": format(rows[i].cash, "f"),
            "exposure": format(rows[i].exposure, "f"),
            "drawdown": dd[i],
        }
        for i in idx
    ]


def fills_json(
    db: Session, run_id: uuid.UUID, limit: int = 1000, offset: int = 0
) -> dict[str, Any]:
    total = db.execute(
        select(func.count()).select_from(SimulatedFill).where(SimulatedFill.run_id == run_id)
    ).scalar_one()
    rows = db.execute(
        select(SimulatedFill)
        .where(SimulatedFill.run_id == run_id)
        .order_by(SimulatedFill.id)
        .limit(limit)
        .offset(offset)
    ).scalars()
    return {
        "total": total,
        "note": "SIMULATED fills (estimated from candle data); not exchange fills",
        "fills": [
            {
                "fill_id": f.fill_id,
                "order_id": f.order_id,
                "symbol": f.symbol,
                "side": f.side,
                "ts": f.ts.isoformat(),
                "price": format(f.price, "f"),
                "quantity": format(f.quantity, "f"),
                "liquidity": f.liquidity,
                "fee_asset": f.fee_asset,
                "fee_amount": format(f.fee_amount, "f"),
                "slippage_cost": format(f.slippage_cost, "f"),
                "realized_pnl": format(f.realized_pnl, "f"),
                "tag": f.tag,
            }
            for f in rows
        ],
    }


def risk_json(db: Session, run_id: uuid.UUID) -> dict[str, Any]:
    run = db.get(BacktestRun, run_id)
    assert run is not None
    cfg = db.get(BacktestRunConfig, run.config_id)
    events = db.execute(
        select(RiskEventRow).where(RiskEventRow.run_id == run_id).order_by(RiskEventRow.seq)
    ).scalars()
    return {
        "verdict": run.risk_verdict,
        "result_status": run.result_status,
        "limits": cfg.config["risk"] if cfg else None,
        "events": [
            {
                "seq": e.seq,
                "ts": e.ts.isoformat(),
                "kind": e.kind,
                "detail": e.detail,
                "equity": format(e.equity, "f"),
            }
            for e in events
        ],
        "note": "a breached budget is reported as failed/stopped regardless of P&L",
    }


def performance_json(db: Session, run_id: uuid.UUID) -> dict[str, Any] | None:
    return db.execute(
        select(PerformanceReportRow.metrics).where(PerformanceReportRow.run_id == run_id)
    ).scalar_one_or_none()


# ------------------------------------------------------------------ validations + holdouts
def save_validation(
    db: Session,
    kind: str,
    strategy_id: str,
    title: str,
    config: dict[str, Any],
    result: dict[str, Any],
    cycle: int,
    oos_reused: bool,
    research_grade: bool,
    advisory: dict[str, Any],
    user: str,
) -> ResearchValidation:
    row = ResearchValidation(
        kind=kind,
        strategy_id=strategy_id,
        title=title,
        config=_jsonify(config),
        result=_jsonify(result),
        cycle=cycle,
        oos_reused=oos_reused,
        research_grade=research_grade,
        advisory=_jsonify(advisory),
        created_by=user,
    )
    db.add(row)
    db.flush()
    return row


def list_validations(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(
        select(ResearchValidation).order_by(ResearchValidation.created_at.desc())
    ).scalars()
    return [validation_json(r, full=False) for r in rows]


def validation_json(r: ResearchValidation, full: bool = True) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": str(r.id),
        "kind": r.kind,
        "strategy_id": r.strategy_id,
        "title": r.title,
        "cycle": r.cycle,
        "oos_reused": r.oos_reused,
        "research_grade": r.research_grade,
        "created_by": r.created_by,
        "created_at": r.created_at.isoformat(),
        "labels": ["SIMULATED", "HISTORICAL DATA", "ESTIMATED FILLS", "NOT LIVE TRADING"],
    }
    if full:
        d.update({"config": r.config, "result": r.result, "advisory": r.advisory})
    else:
        d["warnings"] = (r.result or {}).get("warnings", [])
        d["pooled"] = (r.result or {}).get("pooled")
    return d


def load_registry(db: Session) -> HoldoutRegistry:
    reg = HoldoutRegistry()
    rows = (
        db.execute(select(ResearchHoldoutWindow).order_by(ResearchHoldoutWindow.id)).scalars().all()
    )
    for r in rows:
        reg.consumed.append(ConsumedWindow(r.key, r.window_start, r.window_end, r.cycle, r.kind))
        reg.cycle = max(reg.cycle, r.cycle)
    return reg


def persist_registry(db: Session, reg: HoldoutRegistry, already: int) -> None:
    for c in reg.consumed[already:]:
        db.add(
            ResearchHoldoutWindow(
                key=c.key, window_start=c.start, window_end=c.end, cycle=c.cycle, kind=c.kind
            )
        )
    db.flush()


# ------------------------------------------------------------------ idempotency
class IdempotencyConflict(Exception):
    pass


def request_hash(body: Any) -> str:
    return hashlib.sha256(canonical(body).encode()).hexdigest()


def idempotent_lookup(
    db: Session, key: str, user: str, endpoint: str, rhash: str
) -> tuple[int, dict[str, Any]] | None:
    row = db.get(IdempotencyKey, (key, user, endpoint))
    if row is None:
        return None
    if row.request_hash != rhash:
        raise IdempotencyConflict("Idempotency-Key was used with a different request body")
    return row.status_code, row.response


def idempotent_store(
    db: Session,
    key: str,
    user: str,
    endpoint: str,
    rhash: str,
    status: int,
    response: dict[str, Any],
) -> None:
    db.add(
        IdempotencyKey(
            key=key,
            user_id=user,
            endpoint=endpoint,
            request_hash=rhash,
            status_code=status,
            response=_jsonify(response),
        )
    )
    db.flush()
