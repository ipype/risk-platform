from datetime import date, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import JSON, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

# fields we track for change history
TRACKED_FIELDS = [
    "title",
    "description",
    "causes",
    "consequences",
    "status",
    "probability",
    "impact",
    "impact_scores",
    "risk_level",
    "target_probability",
    "target_impact",
    "target_impact_scores",
    "target_risk_level",
    "mitigation_actions",
    "owner",
    "last_review_date",
    "comments",
    "custom_fields",
]


class RiskHistory(Base):
    """One row per create / update / delete of a risk. Survives risk deletion."""

    __tablename__ = "risk_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    risk_id: Mapped[int] = mapped_column(Integer, index=True)
    #: Matches ``risk.risk_code``, which widened to 100 in migration 0019 when the code
    #: became ``<program>-<project>-<sequence>``. This column is a *copy* taken at write
    #: time and is never rewritten — 0019 widens it so new entries fit and leaves every
    #: existing value exactly as it was recorded, which is what an append-only trail means.
    risk_code: Mapped[str] = mapped_column(String(100), index=True)
    action: Mapped[str] = mapped_column(String(20))  # created / updated / deleted
    actor: Mapped[str] = mapped_column(String(120), default="Unknown")
    changes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


def snapshot(risk: Any) -> dict:
    out: dict = {}
    for f in TRACKED_FIELDS:
        v = getattr(risk, f, None)
        if isinstance(v, date):
            v = v.isoformat()
        out[f] = v
    return out


def diff_snapshots(old: dict, new: dict) -> list[dict]:
    changes: list[dict] = []
    for f in TRACKED_FIELDS:
        if old.get(f) != new.get(f):
            changes.append({"field": f, "old": old.get(f), "new": new.get(f)})
    return changes


def creation_changes(snap: dict) -> list[dict]:
    changes: list[dict] = []
    for f in TRACKED_FIELDS:
        v = snap.get(f)
        if v not in (None, "", {}, []):
            changes.append({"field": f, "old": None, "new": v})
    return changes


class ChangeItem(BaseModel):
    field: str
    old: Any = None
    new: Any = None


class RiskHistoryRead(BaseModel):
    id: int
    risk_id: int
    risk_code: str
    action: str
    actor: str
    changes: list[ChangeItem] | None
    created_at: datetime

    model_config = {"from_attributes": True}
