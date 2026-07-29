"""Everything the mapping feature needs from the database.

The split with ``mapping_suggest`` is deliberate: that module scores, this one loads.
Anything here may touch the session; nothing there may. When the sim engine lands it will
import the scoring module and none of this.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone

from app.models.matrix import get_active_config
from app.models.rbs import RbsCategory, RbsSubcategory
from app.models.risk import Risk
from app.models.schedule import (
    ScheduleActivity,
    ScheduleRelationship,
    ScheduleVersion,
    ScheduleWbs,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mapping import (
    MappingHistory,
    MappingSuggestionOutcome,
    RiskActivityMapping,
    diff_mapping,
    mapping_snapshot,
)
from app.services.mapping_suggest import (
    ActivityCorpus,
    ActivityRow,
    Precedent,
    RiskRow,
    resolve_scope,
    tokenize,
)

#: Statuses that occupy a landing point. A rejected mapping does not, so the same
#: activity can be suggested again later once something about the risk changes.
LIVE_STATUSES = ("proposed", "accepted")

#: Words that identify the schedule impact area in a configurable matrix. The area codes
#: are user-defined, so guessing from the name is the only portable option.
SCHEDULE_AREA_HINTS = ("sched", "time", "delay", "duration")


# --------------------------------------------------------------------------- #
# loading
# --------------------------------------------------------------------------- #


async def wbs_paths(db: AsyncSession, version_id: int) -> dict[str, str]:
    """``wbs_source_id -> "Parent > Child"``, built once per version.

    Cycles in the parent chain are survivable rather than fatal: a malformed export
    should degrade to a short path, not take down the mapping screen.
    """
    rows = (await db.scalars(select(ScheduleWbs).where(ScheduleWbs.version_id == version_id))).all()
    by_id = {r.source_id: r for r in rows}

    def path(source_id: str) -> str:
        parts: list[str] = []
        seen: set[str] = set()
        cur: str | None = source_id
        while cur and cur in by_id and cur not in seen:
            seen.add(cur)
            node = by_id[cur]
            if not node.is_project_node:
                parts.append(node.name or node.code or cur)
            cur = node.parent_source_id
        return " > ".join(reversed(parts))

    return {sid: path(sid) for sid in by_id}


async def load_activities(db: AsyncSession, version_id: int) -> list[ActivityRow]:
    paths = await wbs_paths(db, version_id)
    rows = (
        await db.scalars(select(ScheduleActivity).where(ScheduleActivity.version_id == version_id))
    ).all()
    return [
        ActivityRow(
            source_id=r.source_id,
            code=r.code or "",
            name=r.name or "",
            type=r.type or "",
            status=r.status or "",
            wbs_source_id=r.wbs_source_id,
            wbs_path=paths.get(r.wbs_source_id or "", ""),
            original_duration_days=r.original_duration_days,
            remaining_duration_days=r.remaining_duration_days,
            total_float_days=r.total_float_days,
            is_critical=bool(r.is_critical),
            constraint_type=r.constraint_type or "none",
        )
        for r in rows
    ]


async def load_risk_row(db: AsyncSession, risk_id: int) -> tuple[Risk, RiskRow] | None:
    risk = await db.get(Risk, risk_id)
    if risk is None:
        return None
    sub = await db.get(RbsSubcategory, risk.subcategory_id)
    cat = await db.get(RbsCategory, sub.category_id) if sub else None
    return risk, RiskRow(
        risk_id=risk.id,
        risk_code=risk.risk_code,
        title=risk.title,
        description=risk.description,
        causes=risk.causes,
        consequences=risk.consequences,
        category_code=cat.code if cat else None,
        category_name=cat.name if cat else None,
        subcategory_id=risk.subcategory_id,
        subcategory_name=sub.name if sub else None,
    )


async def load_precedent(db: AsyncSession, subcategory_id: int | None) -> Precedent:
    """Accept/reject counts for this subcategory, learned from past decisions.

    Scoped to the subcategory rather than global: "permit" meaning something in
    Regulatory tells you nothing about what it means in Procurement.
    """
    prec = Precedent()
    if subcategory_id is None:
        return prec
    rows = (
        await db.scalars(
            select(MappingSuggestionOutcome).where(
                MappingSuggestionOutcome.subcategory_id == subcategory_id
            )
        )
    ).all()
    for row in rows:
        target = prec.accepts if row.outcome == "accepted" else prec.rejects
        for token in row.activity_tokens or []:
            target[token] += 1
    return prec


async def live_mappings_for_risk(
    db: AsyncSession, version_id: int, risk_id: int
) -> list[RiskActivityMapping]:
    return list(
        (
            await db.scalars(
                select(RiskActivityMapping)
                .where(
                    RiskActivityMapping.version_id == version_id,
                    RiskActivityMapping.risk_id == risk_id,
                    RiskActivityMapping.status.in_(LIVE_STATUSES),
                )
                .order_by(RiskActivityMapping.id)
            )
        ).all()
    )


async def relationship_pairs(db: AsyncSession, version_id: int) -> set[tuple[str, str]]:
    rows = (
        await db.scalars(
            select(ScheduleRelationship).where(ScheduleRelationship.version_id == version_id)
        )
    ).all()
    return {(r.predecessor_source_id, r.successor_source_id) for r in rows}


# --------------------------------------------------------------------------- #
# audit
# --------------------------------------------------------------------------- #


def log_mapping(
    db: AsyncSession,
    mapping: RiskActivityMapping,
    action: str,
    actor: str,
    changes: list[dict] | None = None,
) -> None:
    """Append a history row. Never mutates an existing one (invariant 5)."""
    db.add(
        MappingHistory(
            mapping_id=mapping.id or 0,
            risk_id=mapping.risk_id,
            version_id=mapping.version_id,
            action=action,
            actor=actor or "Unknown",
            changes=changes,
        )
    )


def snapshot_for_diff(mapping: RiskActivityMapping) -> dict:
    return mapping_snapshot(mapping)


def changes_between(old: dict, new: dict) -> list[dict]:
    return diff_mapping(old, new)


def record_outcome(
    db: AsyncSession,
    *,
    risk: Risk,
    version_id: int,
    activity: ActivityRow | None,
    outcome: str,
    score: float | None,
    actor: str,
) -> None:
    """Feed the precedent signal.

    Written for rejections as well as acceptances — a ranker that only ever sees its own
    accepted output will happily keep recommending the same wrong branch forever.
    """
    if activity is None:
        return
    db.add(
        MappingSuggestionOutcome(
            risk_id=risk.id,
            version_id=version_id,
            subcategory_id=risk.subcategory_id,
            activity_source_id=activity.source_id,
            activity_tokens=tokenize(f"{activity.name} {activity.wbs_path}"),
            outcome=outcome,
            score=score,
            actor=actor or "Unknown",
        )
    )


def stamp_decision(mapping: RiskActivityMapping, actor: str) -> None:
    mapping.decided_by = actor or "Unknown"
    mapping.decided_at = datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# coverage
# --------------------------------------------------------------------------- #


async def schedule_area_code(db: AsyncSession) -> str | None:
    """Which configured impact area means "schedule". None when there is not one.

    Goes through ``get_active_config`` so an install that has never saved a matrix still
    resolves against the shipped 5x5 default instead of reporting no schedule area and
    falling back to "every open risk".
    """
    config = await get_active_config(db)
    for area in config.get("impact_areas", []) or []:
        haystack = f"{area.get('code', '')} {area.get('name', '')}".lower()
        if any(h in haystack for h in SCHEDULE_AREA_HINTS):
            return area.get("code")
    return None


def _schedule_score(risk: Risk, area_code: str | None) -> int | None:
    if not area_code:
        return None
    scores = risk.impact_scores or {}
    value = scores.get(area_code)
    return int(value) if isinstance(value, (int, float)) else None


async def coverage_report(db: AsyncSession, version_id: int) -> dict:
    """Who is mapped, who is not, and which critical work has nothing pointing at it.

    The second half is the one that gets skipped and the one a reviewer asks about: a
    register can be 100% mapped and still leave the driving path untouched.
    """
    activities = await load_activities(db, version_id)
    by_source = {a.source_id: a for a in activities}

    mappings = (
        await db.scalars(
            select(RiskActivityMapping).where(RiskActivityMapping.version_id == version_id)
        )
    ).all()

    by_risk: dict[int, Counter] = defaultdict(Counter)
    covered_activities: set[str] = set()
    for m in mappings:
        by_risk[m.risk_id][m.status] += 1
        if m.status != "accepted":
            continue
        if m.mapping_type == "scoped_driver":
            covered_activities.update(a.source_id for a in resolve_scope(m.scope, activities))
        else:
            for sid in (m.activity_source_id, m.predecessor_source_id, m.successor_source_id):
                if sid:
                    covered_activities.add(sid)

    area_code = await schedule_area_code(db)
    risks = (await db.scalars(select(Risk).order_by(Risk.risk_code))).all()

    in_scope, accepted, proposed_only, unmapped = [], 0, 0, []
    for risk in risks:
        score = _schedule_score(risk, area_code)
        # Without a configured schedule area, fall back to every open risk rather than
        # silently reporting 100% coverage of an empty set.
        relevant = (score or 0) > 0 if area_code else (risk.status or "").lower() == "open"
        if not relevant:
            continue
        in_scope.append(risk)
        counts = by_risk.get(risk.id, Counter())
        if counts.get("accepted"):
            accepted += 1
        elif counts.get("proposed"):
            proposed_only += 1
        else:
            unmapped.append(
                {
                    "risk_id": risk.id,
                    "risk_code": risk.risk_code,
                    "title": risk.title,
                    "schedule_impact": score,
                }
            )

    critical = [a for a in activities if a.is_critical and not a.is_complete]
    critical_uncovered = [
        {
            "activity_source_id": a.source_id,
            "activity_code": a.code,
            "activity_name": a.name,
            "total_float_days": a.total_float_days,
            "remaining_duration_days": a.remaining_duration_days,
        }
        for a in critical
        if a.source_id not in covered_activities
    ]

    total = len(in_scope)
    return {
        "version_id": version_id,
        "schedule_impact_area": area_code,
        "risks_in_scope": total,
        "risks_with_accepted_mapping": accepted,
        "risks_with_proposed_only": proposed_only,
        "risks_unmapped": len(unmapped),
        "coverage_pct": round(100.0 * accepted / total, 1) if total else 0.0,
        "unmapped": unmapped[:200],
        "activities_total": len(activities),
        "activities_covered": len(covered_activities & set(by_source)),
        "critical_activities": len(critical),
        "critical_activities_uncovered": len(critical_uncovered),
        "critical_uncovered": critical_uncovered[:200],
        "mappings_total": len(mappings),
        "mappings_accepted": sum(1 for m in mappings if m.status == "accepted"),
        "mappings_proposed": sum(1 for m in mappings if m.status == "proposed"),
    }


# --------------------------------------------------------------------------- #
# carry-forward
# --------------------------------------------------------------------------- #


async def carry_forward(
    db: AsyncSession,
    *,
    from_version_id: int,
    to_version_id: int,
    actor: str,
    statuses: tuple[str, ...] = ("accepted",),
) -> dict:
    """Re-anchor mappings from an older parse onto a newer one.

    Matched on activity **code**, not ``source_id``. The Primavera task id is a database
    key of whichever P6 instance produced the export; the activity ID is what the planner
    typed and what survives a copy, a restore, or a different EPS. Matching on the wrong
    one produces a carry-forward that appears to work and silently drops every mapping
    the first time the schedule moves between databases.

    Carried rows land as ``proposed`` regardless of what they were. The network changed;
    a human confirms the mapping still means what it meant (invariant 4).
    """
    old_acts = await load_activities(db, from_version_id)
    new_acts = await load_activities(db, to_version_id)
    old_by_source = {a.source_id: a for a in old_acts}
    new_by_code: dict[str, ActivityRow] = {}
    for a in new_acts:
        if a.code:
            new_by_code.setdefault(a.code, a)
    new_by_source = {a.source_id: a for a in new_acts}

    def remap(source_id: str | None) -> str | None:
        if not source_id:
            return None
        old = old_by_source.get(source_id)
        if old and old.code and old.code in new_by_code:
            return new_by_code[old.code].source_id
        # Fall back to the raw id, which holds when both parses came from the same
        # database — but only after the stable key has been tried.
        return source_id if source_id in new_by_source else None

    existing = {
        (
            m.risk_id,
            m.mapping_type,
            m.activity_source_id,
            m.predecessor_source_id,
            m.successor_source_id,
        )
        for m in (
            await db.scalars(
                select(RiskActivityMapping).where(RiskActivityMapping.version_id == to_version_id)
            )
        ).all()
    }

    source_rows = (
        await db.scalars(
            select(RiskActivityMapping).where(
                RiskActivityMapping.version_id == from_version_id,
                RiskActivityMapping.status.in_(statuses),
            )
        )
    ).all()

    carried, dropped, skipped = 0, [], 0
    for old in source_rows:
        if old.mapping_type == "scoped_driver":
            # A filter needs no remapping; it re-resolves against the new version.
            new_activity = new_pred = new_succ = None
            resolved = resolve_scope(old.scope, new_acts)
            if not resolved:
                dropped.append(
                    {
                        "mapping_id": old.id,
                        "risk_id": old.risk_id,
                        "reason": "scope matches nothing in the new version",
                    }
                )
                continue
        else:
            new_activity = remap(old.activity_source_id)
            new_pred = remap(old.predecessor_source_id)
            new_succ = remap(old.successor_source_id)
            required = (
                [old.activity_source_id and new_activity]
                if old.mapping_type == "duration_driver"
                else [new_pred, new_succ]
            )
            if any(r is None for r in required):
                dropped.append(
                    {
                        "mapping_id": old.id,
                        "risk_id": old.risk_id,
                        "reason": "activity no longer present in the new version",
                        "activity_code": (
                            old_by_source.get(old.activity_source_id or "").code
                            if old_by_source.get(old.activity_source_id or "")
                            else None
                        ),
                    }
                )
                continue

        key = (old.risk_id, old.mapping_type, new_activity, new_pred, new_succ)
        if key in existing:
            skipped += 1
            continue

        row = RiskActivityMapping(
            risk_id=old.risk_id,
            version_id=to_version_id,
            mapping_type=old.mapping_type,
            activity_source_id=new_activity,
            predecessor_source_id=new_pred,
            successor_source_id=new_succ,
            scope=old.scope,
            allocation_pct=old.allocation_pct,
            status="proposed",
            origin="carried_forward",
            suggestion_score=old.suggestion_score,
            suggestion_signals=old.suggestion_signals,
            rationale=old.rationale,
            proposed_by=actor or "Unknown",
            carried_from_id=old.id,
        )
        db.add(row)
        await db.flush()
        log_mapping(
            db,
            row,
            "carried_forward",
            actor,
            [{"field": "carried_from", "old": old.id, "new": row.id}],
        )
        existing.add(key)
        carried += 1

    return {
        "from_version_id": from_version_id,
        "to_version_id": to_version_id,
        "carried": carried,
        "skipped_existing": skipped,
        "dropped": dropped,
        "dropped_count": len(dropped),
    }


# --------------------------------------------------------------------------- #
# corpus cache
# --------------------------------------------------------------------------- #


def corpus_for(version_id: int, activities: list[ActivityRow]) -> ActivityCorpus:
    """Build IDF for one version's activity names.

    Deliberately *not* cached. The obvious cache key is ``version_id``, which is unique
    within one database and not across them — so a process-global cache would be a
    correctness trap the moment tenancy is decided, and it would fail silently as
    slightly wrong rankings rather than as an error. The saving would be small in any
    case: the query that loaded these rows already cost more than tokenising them.

    If profiling later says otherwise, key it on a content fingerprint, not the id.
    """
    return ActivityCorpus(activities)


async def version_or_none(db: AsyncSession, version_id: int) -> ScheduleVersion | None:
    return await db.get(ScheduleVersion, version_id)


async def count_mappings(db: AsyncSession, version_id: int) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(RiskActivityMapping)
            .where(RiskActivityMapping.version_id == version_id)
        )
        or 0
    )
