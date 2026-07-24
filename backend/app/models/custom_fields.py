from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import JSON, Boolean, DateTime, String, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class CustomFieldConfig(Base):
    """Definitions of user-added risk fields, stored as one editable JSON document."""

    __tablename__ = "custom_field_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    definition: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FieldDef(BaseModel):
    key: str          # stable machine key, e.g. "field_1"
    label: str        # display name the user types
    type: str         # text | number | date | select
    options: list[str] = []   # for select


class CustomFieldConfigDef(BaseModel):
    fields: list[FieldDef]


DEFAULT_CUSTOM_FIELDS: dict = {"fields": []}


async def get_custom_fields(db: AsyncSession) -> dict:
    res = await db.execute(
        select(CustomFieldConfig)
        .where(CustomFieldConfig.is_active.is_(True))
        .order_by(CustomFieldConfig.id.desc())
    )
    row = res.scalars().first()
    return row.definition if row else DEFAULT_CUSTOM_FIELDS
