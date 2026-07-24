from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.matrix import (
    MatrixConfig,
    MatrixConfigDef,
    band_for,
    get_active_config,
    overall_impact,
)
from app.models.risk import Risk

router = APIRouter(prefix="/matrix-config", tags=["matrix"])


@router.get("")
async def read_config(db: AsyncSession = Depends(get_db)) -> dict:
    return await get_active_config(db)


@router.put("")
async def save_config(
    payload: MatrixConfigDef, db: AsyncSession = Depends(get_db)
) -> dict:
    definition = payload.model_dump()

    res = await db.execute(
        select(MatrixConfig).where(MatrixConfig.is_active.is_(True))
    )
    row = res.scalars().first()
    if row is None:
        row = MatrixConfig(
            name=definition["name"], is_active=True, definition=definition
        )
        db.add(row)
    else:
        row.name = definition["name"]
        row.definition = definition
    await db.flush()

    # re-score every existing risk against the new bands
    risks = (await db.execute(select(Risk))).scalars().all()
    for risk in risks:
        oi = overall_impact(risk.impact_scores, risk.impact)
        risk.risk_level = band_for(risk.probability, oi, definition)

    await db.commit()
    return definition
