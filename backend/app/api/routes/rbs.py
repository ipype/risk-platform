from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.models.rbs import RbsCategory

router = APIRouter(prefix="/rbs", tags=["rbs"])


@router.get("/categories")
async def list_categories(db: AsyncSession = Depends(get_db)) -> list[dict]:
    result = await db.execute(
        select(RbsCategory)
        .options(selectinload(RbsCategory.subcategories))
        .order_by(RbsCategory.sort_order)
    )
    categories = result.scalars().all()
    return [
        {
            "code": c.code,
            "name": c.name,
            "subcategories": [
                {"code": s.code, "name": s.name, "prefix": f"{c.code}-{s.code}"}
                for s in c.subcategories
            ],
        }
        for c in categories
    ]