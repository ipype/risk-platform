"""Removing an imported schedule.

Deletion is the one place the append-only rule has to be read carefully. Invariant 5 says
history is never mutated — it does not say a wrong file must stay on the platform forever.
A test export, a schedule uploaded against the wrong project, a `.xer` someone dragged in
twice under a different name: leaving those in the version list is not an audit trail, it
is noise that makes the real versions harder to find and gives the mapping tab an
ambiguous list to bind against.

So: derived data goes, the record of the decision stays. Activities, relationships, WBS
nodes, calendars and gate runs are all reproducible from the stored bytes and are deleted
outright. Risk-to-activity mappings are *not* reproducible — they are analyst judgement —
so a version carrying accepted mappings refuses to delete until the caller confirms, and
every mapping that does go writes a ``deleted`` row into ``mapping_history`` on the way
out. ``mapping_history`` and ``mapping_suggestion_outcome`` are never touched: they carry
no foreign key precisely so they can outlive what they describe.

Two things are done by hand rather than left to the database, both deliberate:

* **Explicit child deletes.** Every child table declares ``ondelete="CASCADE"``, which
  Postgres honours and SQLite ignores unless ``PRAGMA foreign_keys`` is on. Deleting in
  dependency order here makes the result identical on both, and — more usefully — means
  the counts reported back to the analyst are rows this code actually removed rather than
  a number inferred from what the database was asked to do.
* **Promotion.** ``ScheduleVersion.is_current`` is set at ingest and nothing else moves
  it. Delete the current version of a project and every downstream read that filters on
  ``is_current`` quietly finds nothing, which looks exactly like "no schedule imported".
  The newest surviving version of the same source project is promoted in the same
  transaction.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ScheduleDeleteBlocked
from app.models.mapping import MappingHistory, RiskActivityMapping
from app.models.schedule import (
    DcmaRun,
    ScheduleActivity,
    ScheduleCalendar,
    ScheduleFile,
    ScheduleRelationship,
    ScheduleVersion,
    ScheduleWbs,
)

#: Child tables, in the order they are removed. Order is cosmetic today — none of them
#: reference each other — but it keeps the delete readable as "leaves first, version last"
#: if a child ever gains a child of its own.
_CHILD_TABLES = (
    ("calendars", ScheduleCalendar),
    ("wbs_nodes", ScheduleWbs),
    ("activities", ScheduleActivity),
    ("relationships", ScheduleRelationship),
    ("dcma_runs", DcmaRun),
)


@dataclass(frozen=True)
class DeleteImpact:
    """What deleting one version would cost, counted before anything is removed.

    Exists so the confirmation on screen can state real numbers. A dialog that says
    "this cannot be undone" without saying what *this* is asks the analyst to accept a
    risk nobody has quantified for them.
    """

    version_id: int
    project_name: str
    source_project_id: str
    is_current: bool
    activities: int
    relationships: int
    wbs_nodes: int
    calendars: int
    dcma_runs: int
    mappings_total: int
    mappings_accepted: int
    mappings_proposed: int
    #: Which version becomes current in this version's place. ``None`` when this is the
    #: only version of the project, in which case the project leaves the platform.
    promotes_version_id: int | None
    file_id: int
    filename: str
    file_size_bytes: int
    #: Versions that would still be parsed from this file afterwards.
    file_versions_remaining: int

    @property
    def file_removable(self) -> bool:
        """Whether the stored bytes could go too, once this version has."""
        return self.file_versions_remaining == 0

    @property
    def needs_force(self) -> bool:
        return self.mappings_accepted > 0


@dataclass
class DeleteOutcome:
    version_id: int
    project_name: str
    deleted: dict[str, int] = field(default_factory=dict)
    mapping_history_kept: int = 0
    promoted_version_id: int | None = None
    file_deleted: bool = False
    #: Why the source bytes were kept, when they were. ``None`` when they were removed or
    #: were never asked for.
    file_retained: str | None = None


# --------------------------------------------------------------------------- #
# counting
# --------------------------------------------------------------------------- #


async def _count(db: AsyncSession, model, version_id: int) -> int:
    total = await db.scalar(
        select(func.count()).select_from(model).where(model.version_id == version_id)
    )
    return int(total or 0)


async def _mapping_counts(db: AsyncSession, version_id: int) -> tuple[int, int, int]:
    """``(total, accepted, proposed)`` for one version.

    ``rejected`` and ``superseded`` rows are counted in the total but not called out:
    losing them costs nothing an analyst would want back, and listing four numbers in a
    confirmation buries the one that matters.
    """
    rows = (
        await db.execute(
            select(RiskActivityMapping.status, func.count())
            .where(RiskActivityMapping.version_id == version_id)
            .group_by(RiskActivityMapping.status)
        )
    ).all()
    by_status = {status: int(count) for status, count in rows}
    return (
        sum(by_status.values()),
        by_status.get("accepted", 0),
        by_status.get("proposed", 0),
    )


async def _successor_version_id(
    db: AsyncSession, version: ScheduleVersion
) -> int | None:
    """The newest other version of the same source project, if there is one."""
    return await db.scalar(
        select(ScheduleVersion.id)
        .where(
            ScheduleVersion.source_project_id == version.source_project_id,
            ScheduleVersion.id != version.id,
        )
        .order_by(ScheduleVersion.created_at.desc(), ScheduleVersion.id.desc())
        .limit(1)
    )


async def delete_impact(db: AsyncSession, version: ScheduleVersion) -> DeleteImpact:
    """Count everything that would go, without touching a row."""
    total, accepted, proposed = await _mapping_counts(db, version.id)

    file_row = await db.get(ScheduleFile, version.file_id)
    siblings_on_file = await db.scalar(
        select(func.count())
        .select_from(ScheduleVersion)
        .where(
            ScheduleVersion.file_id == version.file_id,
            ScheduleVersion.id != version.id,
        )
    )

    return DeleteImpact(
        version_id=version.id,
        project_name=version.project_name,
        source_project_id=version.source_project_id,
        is_current=version.is_current,
        activities=await _count(db, ScheduleActivity, version.id),
        relationships=await _count(db, ScheduleRelationship, version.id),
        wbs_nodes=await _count(db, ScheduleWbs, version.id),
        calendars=await _count(db, ScheduleCalendar, version.id),
        dcma_runs=await _count(db, DcmaRun, version.id),
        mappings_total=total,
        mappings_accepted=accepted,
        mappings_proposed=proposed,
        promotes_version_id=(
            await _successor_version_id(db, version) if version.is_current else None
        ),
        file_id=version.file_id,
        filename=file_row.filename if file_row else "(missing)",
        file_size_bytes=file_row.size_bytes if file_row else 0,
        file_versions_remaining=int(siblings_on_file or 0),
    )


# --------------------------------------------------------------------------- #
# deleting
# --------------------------------------------------------------------------- #


async def _log_mapping_deletions(
    db: AsyncSession, version_id: int, actor: str
) -> int:
    """Write one ``deleted`` history row per mapping, then report how many.

    The history row outlives the mapping on purpose (``mapping_history`` carries no
    foreign key). Six months on, "why is there no mapping for REG-010-0001" has an
    answer — who removed the schedule version it was made against, and when — instead of
    a silent gap.
    """
    rows = (
        await db.scalars(
            select(RiskActivityMapping).where(
                RiskActivityMapping.version_id == version_id
            )
        )
    ).all()
    for row in rows:
        db.add(
            MappingHistory(
                mapping_id=row.id,
                risk_id=row.risk_id,
                version_id=row.version_id,
                action="deleted",
                actor=actor or "Unknown",
                changes=[
                    {"field": "status", "old": row.status, "new": None},
                    {
                        "field": "schedule_version",
                        "old": version_id,
                        "new": None,
                    },
                ],
            )
        )
    return len(rows)


async def delete_version(
    db: AsyncSession,
    version: ScheduleVersion,
    *,
    actor: str = "Unknown",
    force: bool = False,
    delete_file: bool = False,
) -> DeleteOutcome:
    """Remove one parsed version and everything derived from it.

    Raises :class:`ScheduleDeleteBlocked` when accepted mappings would be lost and
    ``force`` is not set. Does not commit: the caller owns the transaction, so a failure
    anywhere below leaves the version intact rather than half-deleted.
    """
    impact = await delete_impact(db, version)
    if impact.needs_force and not force:
        raise ScheduleDeleteBlocked(
            version.id, impact.mappings_accepted, impact.mappings_proposed
        )

    outcome = DeleteOutcome(version_id=version.id, project_name=version.project_name)

    outcome.mapping_history_kept = await _log_mapping_deletions(db, version.id, actor)
    mappings = await db.execute(
        delete(RiskActivityMapping).where(
            RiskActivityMapping.version_id == version.id
        )
    )
    outcome.deleted["mappings"] = mappings.rowcount or 0

    for label, model in _CHILD_TABLES:
        result = await db.execute(delete(model).where(model.version_id == version.id))
        outcome.deleted[label] = result.rowcount or 0

    was_current = version.is_current
    file_id = version.file_id
    await db.delete(version)
    await db.flush()

    # Promote after the delete, so the successor lookup cannot pick the row on its way
    # out and leave the project with no current version at all.
    if was_current:
        successor_id = await db.scalar(
            select(ScheduleVersion.id)
            .where(ScheduleVersion.source_project_id == impact.source_project_id)
            .order_by(ScheduleVersion.created_at.desc(), ScheduleVersion.id.desc())
            .limit(1)
        )
        if successor_id is not None:
            successor = await db.get(ScheduleVersion, successor_id)
            if successor is not None:
                successor.is_current = True
                outcome.promoted_version_id = successor.id

    if delete_file:
        remaining = await db.scalar(
            select(func.count())
            .select_from(ScheduleVersion)
            .where(ScheduleVersion.file_id == file_id)
        )
        if remaining:
            outcome.file_retained = (
                f"{int(remaining)} other version(s) were parsed from this file, so the "
                "stored bytes were kept."
            )
        else:
            file_row = await db.get(ScheduleFile, file_id)
            if file_row is None:
                outcome.file_retained = "The stored file was already gone."
            else:
                await db.delete(file_row)
                outcome.file_deleted = True

    await db.flush()
    return outcome
