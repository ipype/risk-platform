from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class Risk(Base):
    __tablename__ = "risk"
    __table_args__ = (
        UniqueConstraint("subcategory_id", "seq", name="uq_risk_subcategory_seq"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    subcategory_id: Mapped[int] = mapped_column(
        ForeignKey("rbs_subcategory.id", ondelete="RESTRICT"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    risk_code: Mapped[str] = mapped_column(String(20), unique=True, index=True)

    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    causes: Mapped[str | None] = mapped_column(Text, nullable=True)
    consequences: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(
        String(30), default="Open", server_default="Open"
    )

    # current (inherent) assessment
    probability: Mapped[int | None] = mapped_column(Integer, nullable=True)
    impact: Mapped[int | None] = mapped_column(Integer, nullable=True)
    impact_scores: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # target (residual) assessment
    target_probability: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_impact: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_impact_scores: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    target_risk_level: Mapped[str | None] = mapped_column(String(20), nullable=True)

    #: Triage flag for the quantitative pass. Set from the matrix, which screens for where
    #: elicitation time is worth spending — it never supplies the numbers themselves.
    #: Managed through the quant routes, not the register PATCH, so triage decisions carry
    #: their own history entries.
    quantify: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    mitigation_actions: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_review_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    custom_fields: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
