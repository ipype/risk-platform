"""Risk-to-activity mapping: suggest, decide, review.

Every write lands as ``proposed`` and a human moves it to ``accepted`` (invariant 4).
That is true whether the suggestion engine put it there or an analyst typed it, so there
is exactly one path into simulation-visible state and one place it is audited.

Validation refuses errors and records warnings. The distinction is the point: driving a
milestone is meaningless and gets a 422, while driving an activity with 90 days of float
is merely probably pointless and is the analyst's call to make and to own.
"""

from __future__ import annotations

from typing import Literal

from app.db.session import get_db
from app.models.risk import Risk
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mapping import MappingHistory, RiskActivityMapping
from app.services.mapping_service import (
    LIVE_STATUSES,
    activity_landings,
    carry_forward,
    changes_between,
    corpus_for,
    coverage_report,
    load_activities,
    load_precedent,
    load_risk_row,
    log_mapping,
    record_outcome,
    relationship_pairs,
    schedule_area_code,
    snapshot_for_diff,
    stamp_decision,
    version_or_none,
)
from app.services.mapping_suggest import (
    SUGGESTER_VERSION,
    ActivityRow,
    has_errors,
    materiality_of,
    resolve_scope,
    suggest,
    validate_duration_driver,
    validate_inserted_activity,
    validate_scope,
)

router = APIRouter(prefix="/mappings", tags=["mappings"])

MappingType = Literal["duration_driver", "inserted_activity", "scoped_driver"]


# --------------------------------------------------------------------------- #
# payloads
# --------------------------------------------------------------------------- #


class ScopeIn(BaseModel):
    field: Literal["wbs", "wbs_path", "activity_type", "name", "code"]
    op: Literal["equals", "starts_with", "contains"] = "equals"
    value: str = Field(min_length=1, max_length=300)


class MappingCreate(BaseModel):
    risk_id: int
    version_id: int
    mapping_type: MappingType
    activity_source_id: str | None = None
    predecessor_source_id: str | None = None
    successor_source_id: str | None = None
    scope: ScopeIn | None = None
    allocation_pct: float | None = Field(default=None, ge=0, le=100)
    rationale: str | None = None
    origin: Literal["suggested", "manual"] = "manual"
    suggestion_score: float | None = None
    suggestion_signals: dict | None = None
    #: Accept immediately instead of landing as a proposal. Still records who decided.
    accept: bool = False

    @model_validator(mode="after")
    def _shape_matches_type(self) -> MappingCreate:
        """Reject a payload whose fields do not belong to its type.

        Silently ignoring a stray ``activity_source_id`` on a scoped driver would leave a
        row that reads as two different mappings depending on who is looking at it.
        """
        t = self.mapping_type
        if t == "duration_driver":
            if not self.activity_source_id:
                raise ValueError("duration_driver requires activity_source_id")
            if self.predecessor_source_id or self.successor_source_id or self.scope:
                raise ValueError("duration_driver takes only activity_source_id")
            if self.allocation_pct is not None:
                raise ValueError(
                    "allocation_pct does not apply to duration_driver: every driven "
                    "activity receives the same sampled factor, which is what makes "
                    "them correlated"
                )
        elif t == "inserted_activity":
            if not (self.predecessor_source_id and self.successor_source_id):
                raise ValueError(
                    "inserted_activity requires predecessor_source_id and successor_source_id"
                )
            if self.activity_source_id or self.scope:
                raise ValueError("inserted_activity takes only predecessor and successor")
        else:
            if self.scope is None:
                raise ValueError("scoped_driver requires scope")
            if self.activity_source_id or self.predecessor_source_id or self.successor_source_id:
                raise ValueError("scoped_driver takes only scope")
            if self.allocation_pct is not None:
                raise ValueError("allocation_pct does not apply to scoped_driver")
        return self


class MappingUpdate(BaseModel):
    status: Literal["proposed", "accepted", "rejected", "superseded"] | None = None
    allocation_pct: float | None = Field(default=None, ge=0, le=100)
    rationale: str | None = None
    scope: ScopeIn | None = None


class BulkAcceptItem(BaseModel):
    activity_source_id: str
    mapping_type: MappingType = "duration_driver"
    suggestion_score: float | None = None
    suggestion_signals: dict | None = None


class BulkAccept(BaseModel):
    risk_id: int
    version_id: int
    items: list[BulkAcceptItem] = Field(min_length=1, max_length=100)
    accept: bool = True


class RejectSuggestion(BaseModel):
    """Recorded even though no mapping is created — rejections train the precedent signal."""

    risk_id: int
    version_id: int
    activity_source_id: str
    score: float | None = None


class CarryForward(BaseModel):
    from_version_id: int
    to_version_id: int
    include_proposed: bool = False


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _actor(x_actor: str | None) -> str:
    return (x_actor or "Unknown").strip()[:120] or "Unknown"


def _read(m: RiskActivityMapping, extra: dict | None = None) -> dict:
    out = {
        "id": m.id,
        "risk_id": m.risk_id,
        "version_id": m.version_id,
        "mapping_type": m.mapping_type,
        "activity_source_id": m.activity_source_id,
        "predecessor_source_id": m.predecessor_source_id,
        "successor_source_id": m.successor_source_id,
        "scope": m.scope,
        "allocation_pct": m.allocation_pct,
        "status": m.status,
        "origin": m.origin,
        "suggestion_score": m.suggestion_score,
        "suggestion_signals": m.suggestion_signals,
        "rationale": m.rationale,
        "proposed_by": m.proposed_by,
        "decided_by": m.decided_by,
        "decided_at": m.decided_at,
        "carried_from_id": m.carried_from_id,
        "created_at": m.created_at,
        "updated_at": m.updated_at,
    }
    if extra:
        out.update(extra)
    return out


async def _require_version(db: AsyncSession, version_id: int):
    version = await version_or_none(db, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail=f"No schedule version {version_id}")
    return version


async def _validate(
    db: AsyncSession,
    *,
    version_id: int,
    mapping_type: str,
    activity_source_id: str | None,
    predecessor_source_id: str | None,
    successor_source_id: str | None,
    scope: dict | None,
    activities: list[ActivityRow] | None = None,
) -> tuple[list[str], dict]:
    """Domain warnings for a proposed mapping, plus context worth showing alongside."""
    acts = activities if activities is not None else await load_activities(db, version_id)
    by_source = {a.source_id: a for a in acts}
    context: dict = {}

    if mapping_type == "duration_driver":
        act = by_source.get(activity_source_id or "")
        if act is None:
            return (
                [f"error: activity {activity_source_id} is not in version {version_id}"],
                context,
            )
        context = {
            "activity_code": act.code,
            "activity_name": act.name,
            "wbs_path": act.wbs_path,
            "materiality": materiality_of(act),
        }
        return validate_duration_driver(act), context

    if mapping_type == "inserted_activity":
        pred = by_source.get(predecessor_source_id or "")
        succ = by_source.get(successor_source_id or "")
        pairs = await relationship_pairs(db, version_id)
        linked = pred is not None and succ is not None and (pred.source_id, succ.source_id) in pairs
        context = {
            "predecessor_name": pred.name if pred else None,
            "successor_name": succ.name if succ else None,
            "existing_link": linked,
            "materiality": materiality_of(succ) if succ else None,
        }
        return validate_inserted_activity(pred, succ, linked), context

    matched = resolve_scope(scope, acts)
    context = {
        "resolved_count": len(matched),
        "resolved_sample": [
            {"activity_source_id": a.source_id, "activity_code": a.code, "activity_name": a.name}
            for a in matched[:10]
        ],
    }
    return validate_scope(matched), context


# --------------------------------------------------------------------------- #
# static paths before /{mapping_id}
# --------------------------------------------------------------------------- #


@router.get("/suggestions")
async def get_suggestions(
    version_id: int = Query(...),
    risk_id: int = Query(...),
    limit: int = Query(default=15, ge=1, le=100),
    min_score: float = Query(default=0.15, ge=0.0, le=1.0),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Ranked landing points for one risk, with the signal breakdown behind each.

    The breakdown is returned rather than just the blended score because "0.62" is not a
    reason and cannot be argued with in a workshop. "Matched permit, regulator; category
    vocabulary hit; no precedent yet" is.
    """
    await _require_version(db, version_id)
    loaded = await load_risk_row(db, risk_id)
    if loaded is None:
        raise HTTPException(status_code=404, detail=f"No risk {risk_id}")
    risk, risk_row = loaded

    activities = await load_activities(db, version_id)
    corpus = corpus_for(version_id, activities)
    precedent = await load_precedent(db, risk.subcategory_id)

    live = (
        await db.scalars(
            select(RiskActivityMapping).where(
                RiskActivityMapping.version_id == version_id,
                RiskActivityMapping.risk_id == risk_id,
                RiskActivityMapping.status.in_(LIVE_STATUSES),
            )
        )
    ).all()
    already = frozenset(m.activity_source_id for m in live if m.activity_source_id)

    by_source = {a.source_id: a for a in activities}
    accepted_wbs = frozenset(
        by_source[m.activity_source_id].wbs_source_id
        for m in live
        if m.status == "accepted"
        and m.activity_source_id
        and m.activity_source_id in by_source
        and by_source[m.activity_source_id].wbs_source_id
    )

    candidates, scope_suggestion = suggest(
        risk_row,
        activities,
        corpus,
        precedent=precedent,
        already_mapped=already,
        accepted_wbs=accepted_wbs,
        limit=limit,
        min_score=min_score,
    )

    return {
        "risk_id": risk_id,
        "risk_code": risk.risk_code,
        "version_id": version_id,
        "suggester_version": SUGGESTER_VERSION,
        "activities_considered": len(activities),
        "precedent_available": precedent.has_evidence,
        "candidates": [c.as_dict() for c in candidates],
        "scope_suggestion": scope_suggestion.as_dict() if scope_suggestion else None,
        "already_mapped": sorted(already),
    }


@router.post("/validate")
async def validate_mapping(payload: MappingCreate, db: AsyncSession = Depends(get_db)) -> dict:
    """Dry run. Same checks as create, no row written — lets the UI warn before saving."""
    await _require_version(db, payload.version_id)
    warnings, context = await _validate(
        db,
        version_id=payload.version_id,
        mapping_type=payload.mapping_type,
        activity_source_id=payload.activity_source_id,
        predecessor_source_id=payload.predecessor_source_id,
        successor_source_id=payload.successor_source_id,
        scope=payload.scope.model_dump() if payload.scope else None,
    )
    return {
        "ok": not has_errors(warnings),
        "warnings": warnings,
        "context": context,
    }


@router.get("/coverage")
async def get_coverage(version_id: int = Query(...), db: AsyncSession = Depends(get_db)) -> dict:
    """Both directions: risks without a mapping, and critical work without a risk.

    The second is the one that gets forgotten. A register can report full coverage while
    the driving path has nothing pointing at it, which is exactly the schedule that comes
    out of Monte Carlo looking reassuringly tight.
    """
    await _require_version(db, version_id)
    return await coverage_report(db, version_id)


@router.get("/activity-landings")
async def get_activity_landings(
    version_id: int = Query(...), db: AsyncSession = Depends(get_db)
) -> dict:
    """Where risks land on the network, keyed by activity, for the Gantt overlay.

    Separate from the Gantt payload on purpose: the schedule read stays free of the
    mapping tables, and a failure here degrades to a chart without badges rather than no
    chart at all.
    """
    await _require_version(db, version_id)
    return await activity_landings(db, version_id)


@router.get("/schedule-area")
async def get_schedule_area(db: AsyncSession = Depends(get_db)) -> dict:
    """Which impact area the coverage report treats as "schedule"."""
    code = await schedule_area_code(db)
    return {"schedule_impact_area": code}


@router.post("/bulk-accept", status_code=201)
async def bulk_accept(
    payload: BulkAccept,
    x_actor: str | None = Header(default=None, alias="X-Actor"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Take several suggestions at once.

    Partial success on purpose: one milestone in a batch of twelve should not cost the
    analyst the other eleven. Rejected entries come back with the reason.
    """
    actor = _actor(x_actor)
    await _require_version(db, payload.version_id)
    loaded = await load_risk_row(db, payload.risk_id)
    if loaded is None:
        raise HTTPException(status_code=404, detail=f"No risk {payload.risk_id}")
    risk, _ = loaded

    activities = await load_activities(db, payload.version_id)
    by_source = {a.source_id: a for a in activities}

    existing = {
        m.activity_source_id
        for m in (
            await db.scalars(
                select(RiskActivityMapping).where(
                    RiskActivityMapping.version_id == payload.version_id,
                    RiskActivityMapping.risk_id == payload.risk_id,
                    RiskActivityMapping.status.in_(LIVE_STATUSES),
                )
            )
        ).all()
        if m.activity_source_id
    }

    created, refused = [], []
    for item in payload.items:
        if item.activity_source_id in existing:
            refused.append(
                {
                    "activity_source_id": item.activity_source_id,
                    "reason": "already mapped to this risk",
                }
            )
            continue
        warnings, _ = await _validate(
            db,
            version_id=payload.version_id,
            mapping_type=item.mapping_type,
            activity_source_id=item.activity_source_id,
            predecessor_source_id=None,
            successor_source_id=None,
            scope=None,
            activities=activities,
        )
        if has_errors(warnings):
            refused.append({"activity_source_id": item.activity_source_id, "reason": warnings[0]})
            continue

        row = RiskActivityMapping(
            risk_id=payload.risk_id,
            version_id=payload.version_id,
            mapping_type=item.mapping_type,
            activity_source_id=item.activity_source_id,
            status="accepted" if payload.accept else "proposed",
            origin="suggested",
            suggestion_score=item.suggestion_score,
            suggestion_signals=item.suggestion_signals,
            proposed_by=actor,
        )
        if payload.accept:
            stamp_decision(row, actor)
        db.add(row)
        await db.flush()
        log_mapping(
            db, row, "created", actor, [{"field": "status", "old": None, "new": row.status}]
        )
        if payload.accept:
            record_outcome(
                db,
                risk=risk,
                version_id=payload.version_id,
                activity=by_source.get(item.activity_source_id),
                outcome="accepted",
                score=item.suggestion_score,
                actor=actor,
            )
        existing.add(item.activity_source_id)
        created.append(_read(row, {"warnings": warnings}))

    await db.commit()
    return {"created": created, "created_count": len(created), "refused": refused}


@router.post("/reject-suggestion", status_code=201)
async def reject_suggestion(
    payload: RejectSuggestion,
    x_actor: str | None = Header(default=None, alias="X-Actor"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Record a dismissed suggestion. No mapping is created; the signal still learns."""
    actor = _actor(x_actor)
    await _require_version(db, payload.version_id)
    loaded = await load_risk_row(db, payload.risk_id)
    if loaded is None:
        raise HTTPException(status_code=404, detail=f"No risk {payload.risk_id}")
    risk, _ = loaded

    activities = await load_activities(db, payload.version_id)
    activity = {a.source_id: a for a in activities}.get(payload.activity_source_id)
    if activity is None:
        raise HTTPException(
            status_code=404,
            detail=f"Activity {payload.activity_source_id} is not in this version",
        )
    record_outcome(
        db,
        risk=risk,
        version_id=payload.version_id,
        activity=activity,
        outcome="rejected",
        score=payload.score,
        actor=actor,
    )
    await db.commit()
    return {"recorded": True}


@router.post("/carry-forward")
async def carry_mappings_forward(
    payload: CarryForward,
    x_actor: str | None = Header(default=None, alias="X-Actor"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Move mappings onto a newer parse of the same project.

    Without this, every re-import silently throws away the mapping work, which on a live
    project is monthly. Carried rows arrive as ``proposed``: the network moved, so the
    mapping is a claim again rather than a decision.
    """
    actor = _actor(x_actor)
    await _require_version(db, payload.from_version_id)
    await _require_version(db, payload.to_version_id)
    if payload.from_version_id == payload.to_version_id:
        raise HTTPException(status_code=422, detail="Source and target are the same version")

    statuses = ("accepted", "proposed") if payload.include_proposed else ("accepted",)
    result = await carry_forward(
        db,
        from_version_id=payload.from_version_id,
        to_version_id=payload.to_version_id,
        actor=actor,
        statuses=statuses,
    )
    await db.commit()
    return result


# --------------------------------------------------------------------------- #
# collection
# --------------------------------------------------------------------------- #


@router.get("")
async def list_mappings(
    version_id: int = Query(...),
    risk_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    mapping_type: str | None = Query(default=None),
    include_context: bool = Query(
        default=True, description="Attach activity names and live validation warnings"
    ),
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await _require_version(db, version_id)
    stmt = select(RiskActivityMapping).where(RiskActivityMapping.version_id == version_id)
    if risk_id is not None:
        stmt = stmt.where(RiskActivityMapping.risk_id == risk_id)
    if status:
        stmt = stmt.where(RiskActivityMapping.status == status)
    if mapping_type:
        stmt = stmt.where(RiskActivityMapping.mapping_type == mapping_type)

    rows = (
        await db.scalars(stmt.order_by(RiskActivityMapping.id).limit(limit).offset(offset))
    ).all()

    if not include_context:
        return {"items": [_read(m) for m in rows], "count": len(rows)}

    # Warnings are computed against the version rather than stored, because the version
    # is immutable — so a recomputed warning can never disagree with the data, and a
    # stored one eventually would.
    activities = await load_activities(db, version_id)
    by_source = {a.source_id: a for a in activities}
    pairs = await relationship_pairs(db, version_id)

    items = []
    for m in rows:
        extra: dict = {}
        if m.mapping_type == "duration_driver":
            act = by_source.get(m.activity_source_id or "")
            if act:
                extra = {
                    "activity_code": act.code,
                    "activity_name": act.name,
                    "wbs_path": act.wbs_path,
                    "materiality": materiality_of(act),
                    "warnings": validate_duration_driver(act),
                }
            else:
                extra = {"warnings": ["error: activity is not in this schedule version"]}
        elif m.mapping_type == "inserted_activity":
            pred = by_source.get(m.predecessor_source_id or "")
            succ = by_source.get(m.successor_source_id or "")
            linked = bool(pred and succ and (pred.source_id, succ.source_id) in pairs)
            extra = {
                "predecessor_name": pred.name if pred else None,
                "successor_name": succ.name if succ else None,
                "existing_link": linked,
                "materiality": materiality_of(succ) if succ else None,
                "warnings": validate_inserted_activity(pred, succ, linked),
            }
        else:
            matched = resolve_scope(m.scope, activities)
            extra = {
                "resolved_count": len(matched),
                "resolved_sample": [
                    {
                        "activity_source_id": a.source_id,
                        "activity_code": a.code,
                        "activity_name": a.name,
                    }
                    for a in matched[:10]
                ],
                "warnings": validate_scope(matched),
            }
        items.append(_read(m, extra))

    return {"items": items, "count": len(items)}


@router.post("", status_code=201)
async def create_mapping(
    payload: MappingCreate,
    x_actor: str | None = Header(default=None, alias="X-Actor"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    actor = _actor(x_actor)
    await _require_version(db, payload.version_id)
    loaded = await load_risk_row(db, payload.risk_id)
    if loaded is None:
        raise HTTPException(status_code=404, detail=f"No risk {payload.risk_id}")
    risk, _ = loaded

    scope = payload.scope.model_dump() if payload.scope else None
    activities = await load_activities(db, payload.version_id)
    warnings, context = await _validate(
        db,
        version_id=payload.version_id,
        mapping_type=payload.mapping_type,
        activity_source_id=payload.activity_source_id,
        predecessor_source_id=payload.predecessor_source_id,
        successor_source_id=payload.successor_source_id,
        scope=scope,
        activities=activities,
    )
    if has_errors(warnings):
        raise HTTPException(
            status_code=422,
            detail={"message": "Mapping is not valid against this schedule", "warnings": warnings},
        )

    duplicate = await db.scalar(
        select(RiskActivityMapping).where(
            RiskActivityMapping.version_id == payload.version_id,
            RiskActivityMapping.risk_id == payload.risk_id,
            RiskActivityMapping.mapping_type == payload.mapping_type,
            RiskActivityMapping.activity_source_id == payload.activity_source_id,
            RiskActivityMapping.predecessor_source_id == payload.predecessor_source_id,
            RiskActivityMapping.successor_source_id == payload.successor_source_id,
            RiskActivityMapping.status.in_(LIVE_STATUSES),
        )
    )
    if duplicate is not None:
        raise HTTPException(
            status_code=409,
            detail=f"This risk already maps here (mapping {duplicate.id})",
        )

    row = RiskActivityMapping(
        risk_id=payload.risk_id,
        version_id=payload.version_id,
        mapping_type=payload.mapping_type,
        activity_source_id=payload.activity_source_id,
        predecessor_source_id=payload.predecessor_source_id,
        successor_source_id=payload.successor_source_id,
        scope=scope,
        allocation_pct=payload.allocation_pct,
        status="accepted" if payload.accept else "proposed",
        origin=payload.origin,
        suggestion_score=payload.suggestion_score,
        suggestion_signals=payload.suggestion_signals,
        rationale=payload.rationale,
        proposed_by=actor,
    )
    if payload.accept:
        stamp_decision(row, actor)
    db.add(row)
    await db.flush()
    log_mapping(db, row, "created", actor, [{"field": "status", "old": None, "new": row.status}])
    if payload.accept and payload.activity_source_id:
        record_outcome(
            db,
            risk=risk,
            version_id=payload.version_id,
            activity={a.source_id: a for a in activities}.get(payload.activity_source_id),
            outcome="accepted",
            score=payload.suggestion_score,
            actor=actor,
        )
    await db.commit()
    await db.refresh(row)
    return _read(row, {"warnings": warnings, **context})


# --------------------------------------------------------------------------- #
# single mapping
# --------------------------------------------------------------------------- #


@router.get("/{mapping_id}")
async def get_mapping(mapping_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    row = await db.get(RiskActivityMapping, mapping_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No mapping {mapping_id}")
    return _read(row)


@router.patch("/{mapping_id}")
async def update_mapping(
    mapping_id: int,
    payload: MappingUpdate,
    x_actor: str | None = Header(default=None, alias="X-Actor"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    actor = _actor(x_actor)
    row = await db.get(RiskActivityMapping, mapping_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No mapping {mapping_id}")

    if payload.allocation_pct is not None and row.mapping_type != "inserted_activity":
        raise HTTPException(
            status_code=422,
            detail=(
                "allocation_pct applies only to inserted_activity; driver mappings share "
                "one sampled factor across every activity they drive"
            ),
        )

    before = snapshot_for_diff(row)
    if payload.status is not None:
        row.status = payload.status
    if payload.allocation_pct is not None:
        row.allocation_pct = payload.allocation_pct
    if payload.rationale is not None:
        row.rationale = payload.rationale
    if payload.scope is not None:
        if row.mapping_type != "scoped_driver":
            raise HTTPException(status_code=422, detail="scope applies only to scoped_driver")
        row.scope = payload.scope.model_dump()

    activities = await load_activities(db, row.version_id)
    warnings, context = await _validate(
        db,
        version_id=row.version_id,
        mapping_type=row.mapping_type,
        activity_source_id=row.activity_source_id,
        predecessor_source_id=row.predecessor_source_id,
        successor_source_id=row.successor_source_id,
        scope=row.scope,
        activities=activities,
    )
    # An accepted mapping must be valid. A proposal may sit in an invalid state while the
    # analyst works out what to do with it.
    if row.status == "accepted" and has_errors(warnings):
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Cannot accept a mapping that fails validation",
                "warnings": warnings,
            },
        )

    changes = changes_between(before, snapshot_for_diff(row))
    if payload.status is not None:
        stamp_decision(row, actor)
        if payload.status in ("accepted", "rejected") and row.activity_source_id:
            risk = await db.get(Risk, row.risk_id)
            if risk is not None:
                record_outcome(
                    db,
                    risk=risk,
                    version_id=row.version_id,
                    activity={a.source_id: a for a in activities}.get(row.activity_source_id),
                    outcome=payload.status,
                    score=row.suggestion_score,
                    actor=actor,
                )
    if changes:
        log_mapping(db, row, "updated", actor, changes)
    await db.commit()
    await db.refresh(row)
    return _read(row, {"warnings": warnings, **context})


@router.delete("/{mapping_id}", status_code=204, response_model=None)
async def delete_mapping(
    mapping_id: int,
    x_actor: str | None = Header(default=None, alias="X-Actor"),
    db: AsyncSession = Depends(get_db),
) -> None:
    actor = _actor(x_actor)
    row = await db.get(RiskActivityMapping, mapping_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No mapping {mapping_id}")
    # History outlives the row it describes, so the delete is still answerable later.
    log_mapping(db, row, "deleted", actor, [{"field": "status", "old": row.status, "new": None}])
    await db.delete(row)
    await db.commit()


@router.get("/{mapping_id}/history")
async def get_mapping_history(
    mapping_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    rows = (
        await db.scalars(
            select(MappingHistory)
            .where(MappingHistory.mapping_id == mapping_id)
            .order_by(MappingHistory.id.desc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "id": r.id,
            "mapping_id": r.mapping_id,
            "risk_id": r.risk_id,
            "version_id": r.version_id,
            "action": r.action,
            "actor": r.actor,
            "changes": r.changes,
            "created_at": r.created_at,
        }
        for r in rows
    ]