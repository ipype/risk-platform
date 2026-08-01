"""Everything that knows the hierarchy is a tree.

The rules the schema cannot hold — containment order, no cycles, work lands on projects,
exactly one default — live here, in one place, applied on every write.

**The whole tree is loaded to answer a question about it.** A recursive CTE would be the
textbook answer and it is the wrong one at this size: a portfolio of a hundred projects is
a hundred rows, one query and a dictionary walk answers every containment question, and
the CTE syntax that Postgres and the SQLite the suite runs on both accept is narrower than
either alone. If a tenant ever arrives with ten thousand nodes this is the function to
change, and nothing outside it will notice.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ScopeInvalid, ScopeNotFound
from app.models.scope import OWNING_KIND, SCOPE_KINDS, SCOPE_RANK, ScopeNode

__all__ = [
    "assert_placement",
    "descendant_ids",
    "ensure_default_project",
    "load_tree",
    "resolve_read_scope",
    "resolve_write_scope",
]

DEFAULT_PROJECT_NAME = "Project"


async def load_tree(db: AsyncSession) -> list[ScopeNode]:
    """Every node, ordered the way a sidebar wants them."""
    rows = await db.scalars(
        select(ScopeNode).order_by(ScopeNode.sort_order, ScopeNode.name, ScopeNode.id)
    )
    return list(rows)


async def ensure_default_project(db: AsyncSession) -> ScopeNode:
    """The node new work lands on when nobody said where. Created on first use.

    A fresh install has no hierarchy and should not have to invent one before adding a
    risk. The first write creates a single project, and a user who never wants a portfolio
    never sees one. Flushed rather than committed: the caller is mid-transaction writing
    the thing that needed the scope, and a commit here would split that in two.
    """
    existing = await db.scalar(select(ScopeNode).where(ScopeNode.is_default.is_(True)))
    if existing is not None:
        return existing

    node = ScopeNode(
        kind=OWNING_KIND,
        parent_id=None,
        name=DEFAULT_PROJECT_NAME,
        is_default=True,
        created_by="system",
    )
    db.add(node)
    await db.flush()
    return node


async def resolve_write_scope(db: AsyncSession, scope_id: int | None) -> ScopeNode:
    """Where an authored row belongs.

    ``None`` means the default project, which is what every single-project install sends.
    A named scope must exist and must be a project: a risk authored at program level would
    have no project to own its audit trail, and a rolled-up risk is a *read* of a project's
    risk rather than a row of its own.
    """
    if scope_id is None:
        return await ensure_default_project(db)

    node = await db.get(ScopeNode, scope_id)
    if node is None:
        raise ScopeNotFound(scope_id)
    if node.kind != OWNING_KIND:
        raise ScopeInvalid(
            f"{node.name!r} is a {node.kind}, and work is authored on projects. "
            "Select a project, or roll this up from the projects beneath it."
        )
    return node


async def resolve_read_scope(db: AsyncSession, scope_id: int | None) -> list[int] | None:
    """The set of scope ids a read should cover, or ``None`` for everything.

    A portfolio reads as itself plus everything under it. ``None`` in and ``None`` out is
    deliberate: an unscoped read is unfiltered, which is what every call site does today
    and what the scope tree (4.8) will start narrowing.
    """
    if scope_id is None:
        return None
    node = await db.get(ScopeNode, scope_id)
    if node is None:
        raise ScopeNotFound(scope_id)
    return await descendant_ids(db, scope_id)


async def descendant_ids(db: AsyncSession, node_id: int) -> list[int]:
    """``node_id`` and every node beneath it, in breadth-first order."""
    nodes = await load_tree(db)
    children: dict[int | None, list[int]] = {}
    for n in nodes:
        children.setdefault(n.parent_id, []).append(n.id)

    out: list[int] = []
    queue = [node_id]
    seen = {node_id}
    while queue:
        current = queue.pop(0)
        out.append(current)
        for child in children.get(current, ()):
            # A cycle cannot be written through this module, but a hand-edited row must
            # not be able to hang the walk that reads it.
            if child not in seen:
                seen.add(child)
                queue.append(child)
    return out


def assert_placement(kind: str, parent: ScopeNode | None) -> None:
    """Containment order, checked before anything is written."""
    if kind not in SCOPE_KINDS:
        raise ScopeInvalid(
            f"{kind!r} is not a scope kind. Use one of {', '.join(SCOPE_KINDS)}."
        )
    if parent is None:
        return
    if SCOPE_RANK[parent.kind] >= SCOPE_RANK[kind]:
        raise ScopeInvalid(
            f"A {kind} cannot sit under a {parent.kind}. The order is "
            "portfolio, then program, then project."
        )


async def assert_move_is_acyclic(
    db: AsyncSession, node_id: int, new_parent_id: int | None
) -> None:
    """A node may not be moved beneath itself.

    Cheap to do and catastrophic to skip: a cycle here detaches a whole subtree from every
    read that walks down from a root, and the rows keep existing while becoming invisible.
    """
    if new_parent_id is None:
        return
    if new_parent_id == node_id:
        raise ScopeInvalid("A scope cannot be its own parent.")
    if new_parent_id in set(await descendant_ids(db, node_id)):
        raise ScopeInvalid(
            "That would move a scope beneath one of its own descendants, which would "
            "detach the subtree from every rollup that reads it."
        )


async def next_sort_order(db: AsyncSession, parent_id: int | None) -> int:
    highest = await db.scalar(
        select(func.coalesce(func.max(ScopeNode.sort_order), -1)).where(
            ScopeNode.parent_id.is_(None)
            if parent_id is None
            else ScopeNode.parent_id == parent_id
        )
    )
    return int(highest or 0) + 1
