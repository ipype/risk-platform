"""Quantitative estimates and correlation drivers.

One row per (risk, scenario). Cost and schedule share that row on purpose: the
occurrence probability lives on it, so a single Bernoulli draw per iteration necessarily
covers both dimensions. Split across two tables and nothing stops a sampler drawing
occurrence twice, producing iterations where a risk hits the cost but not the programme.
That is incoherent, it silently deflates the burn-rate term, and it is invisible in the
output. The schema makes the wrong version awkward to write.

Deliberately *not* derived from the qualitative matrix scores on ``Risk``. Mapping an
ordinal impact band onto a currency range invents precision that no one supplied and
leaves no trace of who supplied it. The matrix triages; this table is elicited.

Money and durations are ``Float``, matching ``MitigationAction.budget``. These are
elicited magnitudes headed for a float64 sampler, never ledger amounts to be reconciled,
so ``Numeric`` would only add Decimal conversions at the boundary.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class RiskQuantEstimate(Base):
    """Three-point cost and schedule impact for one risk under one scenario."""

    __tablename__ = "risk_quant_estimate"
    __table_args__ = (
        UniqueConstraint("risk_id", "scenario", name="uq_quant_risk_scenario"),
        # NULL comparisons yield NULL and pass a CHECK, so these bind only when a
        # dimension is actually populated.
        CheckConstraint("cost_min <= cost_ml", name="ck_quant_cost_min_ml"),
        CheckConstraint("cost_ml <= cost_max", name="ck_quant_cost_ml_max"),
        CheckConstraint("sched_min <= sched_ml", name="ck_quant_sched_min_ml"),
        CheckConstraint("sched_ml <= sched_max", name="ck_quant_sched_ml_max"),
        CheckConstraint(
            "p_occurrence > 0 AND p_occurrence <= 1", name="ck_quant_p_occurrence"
        ),
        CheckConstraint("pert_lambda > 0", name="ck_quant_pert_lambda"),
        Index("ix_quant_risk_scenario", "risk_id", "scenario"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    risk_id: Mapped[int] = mapped_column(
        ForeignKey("risk.id", ondelete="CASCADE"), index=True
    )
    scenario: Mapped[str] = mapped_column(
        String(20), server_default="pre_mitigation", default="pre_mitigation"
    )

    # occurrence — continuous, and not the matrix probability band
    p_occurrence: Mapped[float] = mapped_column(Float, default=1.0)
    #: True for inherent range on a base estimate rather than a discrete event. Forces
    #: p_occurrence to 1.0. AACE 57R-09 keeps these apart; without the flag a register
    #: quietly turns into an estimate.
    is_variability: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    #: What the elicited min and max actually mean. Load-bearing: treating a P10/P90 pair
    #: as hard bounds truncates the tail the contingency is there to cover.
    bound_interpretation: Mapped[str] = mapped_column(
        String(20), default="absolute", server_default="absolute"
    )
    dist_type: Mapped[str] = mapped_column(
        String(20), default="pert", server_default="pert"
    )
    pert_lambda: Mapped[float] = mapped_column(Float, default=4.0, server_default="4.0")

    # cost impact — signed, so opportunities are negative and the sampler needs no branch
    cost_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_ml: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_basis: Mapped[str] = mapped_column(
        String(20), default="absolute", server_default="absolute"
    )

    # schedule impact, in days
    sched_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    sched_ml: Mapped[float | None] = mapped_column(Float, nullable=True)
    sched_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Working vs calendar days. Never inferred — the two differ by ~40% and the error is
    #: invisible in the output.
    sched_day_basis: Mapped[str] = mapped_column(
        String(20), default="working", server_default="working"
    )

    source: Mapped[str] = mapped_column(String(20), default="sme", server_default="sme")
    confidence: Mapped[str] = mapped_column(
        String(20), default="medium", server_default="medium"
    )
    estimated_by: Mapped[str] = mapped_column(
        String(120), default="Unknown", server_default="Unknown"
    )
    estimated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Frozen against a simulation run. Reproducibility invariant: a run's inputs cannot
    #: move underneath it after the fact.
    locked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RiskDriver(Base):
    """A shared cause used to build the correlation matrix.

    Pairwise correlation elicitation is O(n^2) and collapses past roughly fifteen risks.
    Tagging risks with the driver they share is O(n) for the analyst and reconstructs the
    matrix mechanically: two risks driven by the same thing correlate at that driver's
    coefficient.
    """

    __tablename__ = "risk_driver"
    __table_args__ = (
        CheckConstraint(
            "correlation_default >= -1 AND correlation_default <= 1",
            name="ck_driver_correlation_range",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_default: Mapped[float] = mapped_column(
        Float, default=0.5, server_default="0.5"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RiskDriverLink(Base):
    """Many-to-many between risks and drivers."""

    __tablename__ = "risk_driver_link"
    __table_args__ = (
        UniqueConstraint("risk_id", "driver_id", name="uq_risk_driver_link"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    risk_id: Mapped[int] = mapped_column(
        ForeignKey("risk.id", ondelete="CASCADE"), index=True
    )
    driver_id: Mapped[int] = mapped_column(
        ForeignKey("risk_driver.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


QUANT_FIELDS = [
    "scenario",
    "p_occurrence",
    "is_variability",
    "bound_interpretation",
    "dist_type",
    "pert_lambda",
    "cost_min",
    "cost_ml",
    "cost_max",
    "cost_basis",
    "sched_min",
    "sched_ml",
    "sched_max",
    "sched_day_basis",
    "source",
    "confidence",
    "notes",
    "locked",
]


def quant_snapshot(e: RiskQuantEstimate) -> dict:
    return {f: getattr(e, f, None) for f in QUANT_FIELDS}


def quant_diff(old: dict, new: dict) -> list[dict]:
    return [
        {"field": f, "old": old.get(f), "new": new.get(f)}
        for f in QUANT_FIELDS
        if old.get(f) != new.get(f)
    ]
