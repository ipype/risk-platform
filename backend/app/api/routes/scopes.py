"""The hierarchy, as an API.

Read is one call returning every node flat with its ``parent_id``. The tree is assembled by
the caller, because the sidebar needs it as a tree, a breadcrumb needs it as a path, and a
scope picker needs it as a list — shipping one shape and letting the client fold it beats
three endpoints that can disagree.

Every refusal names the thing to do instead. Deleting a portfolio that still holds
programs, moving a program under a project, authoring at portfolio level: all of these are
decisions someone can make differently, not faults they have to report.
"""

# No `from __future__ import annotations` in this module, deliberately. Under postponed
# evaluation FastAPI reads a `-> None` return annotation as a response body and refuses to
# register any route declaring 204, which is how `DELETE /scopes/{id}` looks. The other
# route modules do not use it either; this comment exists so nobody adds it back.

from datetime import datetime

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ScopeDeleteBlocked, ScopeInvalid, ScopeNotFound
from app.db.session import get_db
from app.models.risk import Risk
from app.models.schedule import ScheduleFile
from app.models.scope import OWNING_KIND, SCOPE_KINDS, ScopeNode
from app.models.simulation import SimulationRun
from app.services.scope import (
    assert_move_is_acyclic,
    assert_placement,
    descendant_ids,
    ensure_default_project,
    load_tree,
    next_sort_order,
)

router = APIRouter(prefix="/scopes", tags=["scopes"])


class ScopeRead(BaseModel):
    id: int
    kind: str
    parent_id: int | None
    name: str
    code: str | None
    description: str | None
    is_default: bool
    sort_order: int
    created_by: str
    created_at: datetime

    #: Rows this node owns directly. Zero on programs and portfolios by construction —
    #: work is authored on projects — and the delete guard reads them.
    risk_count: int = 0
    schedule_file_count: int = 0
    run_count: int = 0
    child_count: int = 0


class ScopeCreate(BaseModel):
    kind: str = Field(..., examples=["project"])
    name: str = Field(..., min_length=1, max_length=200)
    parent_id: int | None = None
    code: str | None = Field(default=None, max_length=40)
    description: str | None = None


class ScopeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    code: str | None = Field(default=None, max_length=40)
    description: str | None = None
    sort_order: int | None = None
    #: Present and null moves the node to the root; absent leaves it where it is.
    parent_id: int | None = None


async def _counts(db: AsyncSession) -> dict[int, dict[str, int]]:
    out: dict[int, dict[str, int]] = {}

    async def tally(column, key: str) -> None:
        rows = await db.execute(select(column, func.count()).group_by(column))
        for scope_id, count in rows:
            out.setdefault(scope_id, {})[key] = int(count)

    await tally(Risk.scope_id, "risk_count")
    await tally(ScheduleFile.scope_id, "schedule_file_count")
    await tally(SimulationRun.scope_id, "run_count")
    await tally(ScopeNode.parent_id, "child_count")
    return out


def _read(node: ScopeNode, counts: dict[int, dict[str, int]]) -> ScopeRead:
    mine = counts.get(node.id, {})
    return ScopeRead(
        id=node.id,
        kind=node.kind,
        parent_id=node.parent_id,
        name=node.name,
        code=node.code,
        description=node.description,
        is_default=bool(node.is_default),
        sort_order=node.sort_order,
        created_by=node.created_by,
        created_at=node.created_at,
        risk_count=mine.get("risk_count", 0),
        schedule_file_count=mine.get("schedule_file_count", 0),
        run_count=mine.get("run_count", 0),
        child_count=mine.get("child_count", 0),
    )


@router.get("", response_model=list[ScopeRead])
async def list_scopes(db: AsyncSession = Depends(get_db)) -> list[ScopeRead]:
    """Every node, flat, with the counts a sidebar and a delete guard both want.

    Creates the default project when the tree is empty, so a fresh install opens on
    something rather than on nothing. This is the only read in the platform that writes,
    and it does so for the same reason the write path does: an install with no scope is a
    state nobody should have to resolve by hand.
    """
    nodes = await load_tree(db)
    if not nodes:
        await ensure_default_project(db)
        await db.commit()
        nodes = await load_tree(db)
    counts = await _counts(db)
    return [_read(n, counts) for n in nodes]


@router.post("", response_model=ScopeRead, status_code=201)
async def create_scope(
    payload: ScopeCreate,
    db: AsyncSession = Depends(get_db),
    actor: str = Header(default="Unknown", alias="X-Actor"),
) -> ScopeRead:
    parent = None
    if payload.parent_id is not None:
        parent = await db.get(ScopeNode, payload.parent_id)
        if parent is None:
            raise ScopeNotFound(payload.parent_id)
    assert_placement(payload.kind, parent)

    if payload.code is not None:
        # The database enforces uniqueness too; checking first turns the constraint
        # violation into the same named, actionable error every other refusal in this
        # router gives, instead of a raw IntegrityError reaching the client as a 500.
        existing = await db.scalar(select(ScopeNode).where(ScopeNode.code == payload.code))
        if existing is not None:
            raise ScopeInvalid(f"Code {payload.code!r} is already in use.")

    node = ScopeNode(
        kind=payload.kind,
        parent_id=payload.parent_id,
        name=payload.name,
        code=payload.code,
        description=payload.description,
        sort_order=await next_sort_order(db, payload.parent_id),
        created_by=actor,
    )
    db.add(node)
    await db.commit()
    await db.refresh(node)
    return _read(node, await _counts(db))


@router.patch("/{scope_id}", response_model=ScopeRead)
async def update_scope(
    scope_id: int,
    payload: ScopeUpdate,
    db: AsyncSession = Depends(get_db),
) -> ScopeRead:
    node = await db.get(ScopeNode, scope_id)
    if node is None:
        raise ScopeNotFound(scope_id)

    fields = payload.model_dump(exclude_unset=True)

    if "parent_id" in fields:
        new_parent_id = fields["parent_id"]
        parent = None
        if new_parent_id is not None:
            parent = await db.get(ScopeNode, new_parent_id)
            if parent is None:
                raise ScopeNotFound(new_parent_id)
        assert_placement(node.kind, parent)
        await assert_move_is_acyclic(db, scope_id, new_parent_id)
        node.parent_id = new_parent_id

    for key in ("name", "code", "description", "sort_order"):
        if key in fields:
            setattr(node, key, fields[key])

    await db.commit()
    await db.refresh(node)
    return _read(node, await _counts(db))


@router.delete("/{scope_id}", status_code=204)
async def delete_scope(scope_id: int, db: AsyncSession = Depends(get_db)) -> None:
    """Refused while anything still points at the node.

    Never cascading. A scope is a filing decision and its contents are the work; deleting
    the folder must not delete a register, a schedule or the append-only record of a run
    (invariant 5). The refusal lists what is in the way so the next step is obvious.
    """
    node = await db.get(ScopeNode, scope_id)
    if node is None:
        raise ScopeNotFound(scope_id)

    counts = (await _counts(db)).get(scope_id, {})
    reasons: list[str] = []
    if counts.get("child_count"):
        reasons.append(f"it still contains {counts['child_count']} scope(s)")
    if counts.get("risk_count"):
        reasons.append(f"{counts['risk_count']} risk(s) belong to it")
    if counts.get("schedule_file_count"):
        reasons.append(f"{counts['schedule_file_count']} schedule file(s) belong to it")
    if counts.get("run_count"):
        reasons.append(f"{counts['run_count']} simulation run(s) belong to it")
    if node.is_default:
        reasons.append(
            "it is the default project, which is where unscoped work lands — make "
            "another project the default first"
        )
    if reasons:
        raise ScopeDeleteBlocked(scope_id, node.name, reasons)

    await db.delete(node)
    await db.commit()


@router.post("/{scope_id}/default", response_model=ScopeRead)
async def set_default(scope_id: int, db: AsyncSession = Depends(get_db)) -> ScopeRead:
    """Move the default flag. Only a project can hold it, since only a project owns work."""
    node = await db.get(ScopeNode, scope_id)
    if node is None:
        raise ScopeNotFound(scope_id)
    if node.kind != OWNING_KIND:
        raise ScopeInvalid(
            f"{node.name!r} is a {node.kind}. The default is where unscoped work lands, "
            "so it has to be a project."
        )

    current = await db.scalar(select(ScopeNode).where(ScopeNode.is_default.is_(True)))
    if current is not None and current.id != node.id:
        # Cleared and flushed before the new one is set: the uniqueness of the default is
        # a database constraint, and setting two before clearing one trips it.
        current.is_default = None
        await db.flush()
    node.is_default = True
    await db.commit()
    await db.refresh(node)
    return _read(node, await _counts(db))


@router.get("/{scope_id}/subtree", response_model=list[int])
async def subtree(scope_id: int, db: AsyncSession = Depends(get_db)) -> list[int]:
    """The node and everything under it. What a scoped read will filter on (4.8)."""
    if await db.get(ScopeNode, scope_id) is None:
        raise ScopeNotFound(scope_id)
    return await descendant_ids(db, scope_id)


@router.get("/kinds", response_model=list[str])
async def kinds(_: int | None = Query(default=None)) -> list[str]:
    return list(SCOPE_KINDS)
