from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base


class RbsCategory(Base):
    """Top-level risk category -- the CCC code, e.g. 'ENV'."""

    __tablename__ = "rbs_category"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(3), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    subcategories: Mapped[list["RbsSubcategory"]] = relationship(
        back_populates="category",
        cascade="all, delete-orphan",
        order_by="RbsSubcategory.sort_order",
    )


class RbsSubcategory(Base):
    """Second-level subcategory -- the DDD code, e.g. '010', unique within a category."""

    __tablename__ = "rbs_subcategory"
    __table_args__ = (
        UniqueConstraint("category_id", "code", name="uq_subcategory_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("rbs_category.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(3))
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    #: Eager rather than lazy. ``Risk.subcategory_prefix`` needs the category code on every
    #: register read, and a subcategory reached through the async session with a lazy
    #: parent raises ``MissingGreenlet`` the moment anything touches it. The extra SELECT
    #: is one query against a table with tens of rows, which is cheaper than every call
    #: site remembering to eager-load it. Two rows of the RBS are the whole cost.
    category: Mapped["RbsCategory"] = relationship(
        back_populates="subcategories", lazy="selectin"
    )
