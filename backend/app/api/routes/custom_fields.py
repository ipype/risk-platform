from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.custom_fields import (
    CustomFieldConfig,
    CustomFieldConfigDef,
    get_custom_fields,
)

router = APIRouter(prefix="/custom-fields", tags=["custom-fields"])


@router.get("")
async def read_fields(db: AsyncSession = Depends(get_db)) -> dict:
    return await get_custom_fields(db)


@router.put("")
async def save_fields(
    payload: CustomFieldConfigDef, db: AsyncSession = Depends(get_db)
) -> dict:
    # keep existing keys stable; assign field_N to any new field
    used = []
    for f in payload.fields:
        if f.key.startswith("field_"):
            try:
                used.append(int(f.key.split("_", 1)[1]))
            except ValueError:
                pass
    counter = max(used, default=0)

    out = []
    for f in payload.fields:
        key = f.key
        if not key:
            counter += 1
            key = f"field_{counter}"
        out.append(
            {"key": key, "label": f.label, "type": f.type, "options": f.options}
        )
    definition = {"fields": out}

    res = await db.execute(
        select(CustomFieldConfig).where(CustomFieldConfig.is_active.is_(True))
    )
    row = res.scalars().first()
    if row is None:
        row = CustomFieldConfig(is_active=True, definition=definition)
        db.add(row)
    else:
        row.definition = definition
    await db.commit()
    return definition
