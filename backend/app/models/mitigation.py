from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class MitigationAction(Base):
    """A single mitigation action belonging to a risk."""

    __tablename__ = "mitigation_action"

    id: Mapped[int] = mapped_column(primary_key=True)
    risk_id: Mapped[int] = mapped_column(
        ForeignKey("risk.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[str] = mapped_column(Text, default="")
    owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    budget: Mapped[float | None] = mapped_column(Float, nullable=True)
    completion_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    effectiveness: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(
        String(30), default="Proposed", server_default="Proposed"
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


ACTION_FIELDS = [
    "action",
    "owner",
    "due_date",
    "budget",
    "completion_pct",
    "effectiveness",
    "status",
]


def action_snapshot(a: MitigationAction) -> dict:
    out: dict = {}
    for f in ACTION_FIELDS:
        v = getattr(a, f, None)
        if isinstance(v, date):
            v = v.isoformat()
        out[f] = v
    return out


def action_diff(old: dict, new: dict) -> list[dict]:
    changes: list[dict] = []
    for f in ACTION_FIELDS:
        if old.get(f) != new.get(f):
            changes.append({"field": f, "old": old.get(f), "new": new.get(f)})
    return changes
