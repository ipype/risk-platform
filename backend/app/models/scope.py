"""Portfolio, program and project as one table.

One node table rather than three, because every scoped row needs exactly one foreign key
and every question anyone asks of the hierarchy — "everything under this node" — is the
same walk whatever the node is. Three tables would mean three nullable columns on every
scoped table, a three-way check that exactly one is set, and a different join per level.
The cost of the single table is that "a program's parent must be a portfolio" is a rule
rather than a schema shape; it is enforced in ``services/scope.py`` on every write and
stated here so nobody has to go looking for it.

**One parent, always.** A project belongs to one program or to one portfolio, never to
two. This is the tenancy decision under a different name (``REFERENCE.md`` 2026-08-01) and
it is what makes a rollup a tree walk instead of a graph traversal with double-counting.

**Depth is a maximum, not a requirement.** A node of any kind may be a root. A single
project with no portfolio above it is the shape of every install on day one, and forcing
an invented portfolio around it would be ceremony. What is fixed is the *order*: a
portfolio may contain programs and projects, a program may contain projects, and nothing
contains a portfolio.

**Work lands on projects.** Registers, schedules and runs are owned by a project node.
Programs and portfolios are where results are read together, never where a risk is
authored — a rolled-up risk edited at program level would fork its own audit trail.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

#: Containment order. A node may only be placed under one of strictly lower rank.
SCOPE_RANK: dict[str, int] = {"portfolio": 0, "program": 1, "project": 2}

SCOPE_KINDS: tuple[str, ...] = ("portfolio", "program", "project")

#: The kind that owns authored work. Registers, schedule uploads and runs land here.
OWNING_KIND = "project"


class ScopeNode(Base):
    """One node of the hierarchy."""

    __tablename__ = "scope_node"

    id: Mapped[int] = mapped_column(primary_key=True)

    kind: Mapped[str] = mapped_column(String(20), index=True)

    #: ``RESTRICT`` rather than ``CASCADE``: deleting a portfolio must never take a
    #: project's register with it. The API refuses a delete with children and says so.
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("scope_node.id", ondelete="RESTRICT"), nullable=True, index=True
    )

    name: Mapped[str] = mapped_column(String(200))
    #: A short human label for reports and codes. Optional, unique when set.
    code: Mapped[str | None] = mapped_column(String(40), nullable=True, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: ``True`` on the one node new work lands on when no scope is named, ``NULL``
    #: everywhere else. Nullable and unique rather than boolean and unique: every database
    #: here permits many nulls in a unique column and exactly one true, so "there is at
    #: most one default" is a constraint the database keeps rather than a rule the
    #: application remembers. A plain boolean would need a partial index and two dialects'
    #: worth of syntax to say the same thing.
    is_default: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, unique=True, default=None
    )

    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    created_by: Mapped[str] = mapped_column(String(120), default="Unknown")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ScopeNode {self.kind} {self.id} {self.name!r}>"
