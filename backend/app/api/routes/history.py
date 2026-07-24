from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.history import RiskHistory, RiskHistoryRead

router = APIRouter(prefix="/history", tags=["history"])


@router.get("", response_model=list[RiskHistoryRead])
async def recent_activity(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[RiskHistory]:
    res = await db.execute(
        select(RiskHistory)
        .order_by(RiskHistory.created_at.desc(), RiskHistory.id.desc())
        .limit(limit)
    )
    return list(res.scalars().all())
