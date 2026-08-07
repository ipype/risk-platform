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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

# Imported for its side effect on the mapper registry as much as for the annotation: the
# ``subcategory`` relationship below resolves ``RbsSubcategory`` by name at configuration
# time, and a module that imports ``Risk`` without ever importing ``rbs`` would otherwise
# fail on first use rather than at import. ``rbs`` imports nothing from here, so there is
# no cycle to unpick.
from app.models.rbs import RbsSubcategory


class Risk(Base):
    __tablename__ = "risk"
    #: Uniqueness is per scope, and that is a product decision rather than a schema
    #: detail: every project's register starts at 0001. A globally unique ``risk_code``
    #: would mean the second project's first risk came out as 0007 because another project
    #: happened to get there first, which is not a register anyone would sign.
    #:
    #: ``seq`` carries no constraint of its own. It is the input to ``risk_code`` and
    #: nothing else, so a duplicate ``seq`` within a scope produces a duplicate
    #: ``risk_code`` within that scope and is refused here. Stating it twice would add a
    #: second thing to keep in step with the code generator for no extra guarantee.
    #: (Migration 0019 dropped ``uq_risk_scope_subcategory_seq``, which sequenced per
    #: subcategory back when the taxonomy was part of the identifier.)
    __table_args__ = (UniqueConstraint("scope_id", "risk_code", name="uq_risk_scope_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)

    #: The project this risk belongs to. ``RESTRICT``: a scope that still owns a register
    #: is not deletable, and the API says which rows are in the way.
    scope_id: Mapped[int] = mapped_column(
        ForeignKey("scope_node.id", ondelete="RESTRICT"), index=True
    )

    subcategory_id: Mapped[int] = mapped_column(
        ForeignKey("rbs_subcategory.id", ondelete="RESTRICT"), index=True
    )
    #: Position in the owning project's register. Allocated by ``services/risk_code.py``.
    seq: Mapped[int] = mapped_column(Integer)
    #: ``<program>-<project>-<sequence>``. Long enough for two 40-character scope codes,
    #: a separator each and the sequence; the schema does not truncate what a user typed.
    risk_code: Mapped[str] = mapped_column(String(100), index=True)

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

    #: Eager and view-only. Eager because the register API returns the taxonomy prefix on
    #: every read and a lazy load would be IO from inside a running event loop; view-only
    #: because ``subcategory_id`` is the writable side and two ways to set the same thing
    #: is how they end up disagreeing.
    subcategory: Mapped["RbsSubcategory"] = relationship(lazy="selectin", viewonly=True)

    @property
    def subcategory_prefix(self) -> str:
        """``ENV-030``. Where the taxonomy lives now that the code no longer carries it.

        Empty rather than raising when the relationship is not loaded: a serialiser
        reaching a half-detached row should produce a thin response, not a 500.
        """
        sub = self.subcategory
        if sub is None or sub.category is None:
            return ""
        return f"{sub.category.code}-{sub.code}"
