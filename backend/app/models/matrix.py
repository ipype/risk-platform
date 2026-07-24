from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import JSON, Boolean, DateTime, String, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class MatrixConfig(Base):
    """The active scoring scheme, stored as one editable JSON document."""

    __tablename__ = "matrix_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), default="Default")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    definition: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ---- Pydantic shape used to validate saves ----
class LevelDef(BaseModel):
    level: int
    label: str


class AreaDef(BaseModel):
    code: str
    name: str
    descriptors: dict[str, str] = {}


class BandDef(BaseModel):
    name: str
    min_score: int
    max_score: int
    color: str


class MatrixConfigDef(BaseModel):
    name: str
    probability_levels: list[LevelDef]
    impact_levels: list[LevelDef]
    impact_areas: list[AreaDef]
    bands: list[BandDef]


# ---- default scheme (used until the user saves their own) ----
DEFAULT_CONFIG: dict = {
    "name": "Default 5x5",
    "probability_levels": [
        {"level": 1, "label": "Rare"},
        {"level": 2, "label": "Unlikely"},
        {"level": 3, "label": "Possible"},
        {"level": 4, "label": "Likely"},
        {"level": 5, "label": "Almost certain"},
    ],
    "impact_levels": [
        {"level": 1, "label": "Negligible"},
        {"level": 2, "label": "Minor"},
        {"level": 3, "label": "Moderate"},
        {"level": 4, "label": "Major"},
        {"level": 5, "label": "Severe"},
    ],
    "impact_areas": [
        {"code": "COST", "name": "Cost", "descriptors": {
            "1": "< $50k", "2": "$50k - $250k", "3": "$250k - $1M",
            "4": "$1M - $5M", "5": "> $5M"}},
        {"code": "SCHED", "name": "Schedule", "descriptors": {
            "1": "< 1 week", "2": "1 - 4 weeks", "3": "1 - 3 months",
            "4": "3 - 6 months", "5": "> 6 months"}},
        {"code": "SAFE", "name": "Safety", "descriptors": {
            "1": "First aid", "2": "Medical treatment", "3": "Lost-time injury",
            "4": "Serious / permanent injury", "5": "Fatality"}},
        {"code": "REP", "name": "Reputation", "descriptors": {
            "1": "Internal only", "2": "Local awareness", "3": "Local media",
            "4": "National media", "5": "Sustained national / political"}},
        {"code": "ENV", "name": "Environment", "descriptors": {
            "1": "Negligible", "2": "Minor, contained", "3": "Moderate, reversible",
            "4": "Major, prolonged", "5": "Severe, irreversible"}},
    ],
    "bands": [
        {"name": "Low", "min_score": 1, "max_score": 4, "color": "#c0dd97"},
        {"name": "Medium", "min_score": 5, "max_score": 9, "color": "#fac775"},
        {"name": "High", "min_score": 10, "max_score": 14, "color": "#f0997b"},
        {"name": "Very high", "min_score": 15, "max_score": 25, "color": "#f09595"},
    ],
}


# ---- scoring helpers (shared by the risks and matrix routes) ----
async def get_active_config(db: AsyncSession) -> dict:
    res = await db.execute(
        select(MatrixConfig)
        .where(MatrixConfig.is_active.is_(True))
        .order_by(MatrixConfig.id.desc())
    )
    row = res.scalars().first()
    return row.definition if row else DEFAULT_CONFIG


def overall_impact(
    impact_scores: dict | None, fallback: int | None
) -> int | None:
    """Worst-case: the highest score across the assessed areas."""
    if impact_scores:
        values = [v for v in impact_scores.values() if isinstance(v, int)]
        if values:
            return max(values)
    return fallback


def band_for(
    probability: int | None, impact: int | None, config: dict
) -> str | None:
    if probability is None or impact is None:
        return None
    score = probability * impact
    for band in config.get("bands", []):
        if band["min_score"] <= score <= band["max_score"]:
            return band["name"]
    return None
