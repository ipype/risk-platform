"""Quantitative estimates and correlation drivers.

One row per (risk, scenario). Cost and schedule share that row on purpose: the occurrence
probability lives on it, so a single Bernoulli draw per iteration necessarily covers both
dimensions. Split across two tables and nothing stops a sampler drawing occurrence twice,
producing iterations where a risk hits the cost but not the programme. That is incoherent,
it silently deflates the burn-rate term, and it is invisible in the output. The schema
makes the wrong version awkward to write.

Deliberately *not* derived from the qualitative matrix scores on ``Risk``. Mapping an
ordinal impact band onto a currency range invents precision nobody supplied and leaves no
trace of who supplied it. The matrix triages; this table is elicited.

Money and durations are ``Float``, matching ``MitigationAction.budget``. These are
elicited magnitudes headed for a float64 sampler, never ledger amounts to be reconciled,
so ``Numeric`` would only add Decimal conversions at the boundary.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
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
    """Cost and schedule impact for one risk under one scenario."""

    __tablename__ = "risk_quant_estimate"
    __table_args__ = (
        UniqueConstraint("risk_id", "scenario", name="uq_quant_risk_scenario"),
        # NULL comparisons yield NULL and pass a CHECK, so ordering binds only when a
        # dimension is actually populated. Uniform leaves ml NULL and is unaffected.
        CheckConstraint("cost_min <= cost_ml", name="ck_quant_cost_min_ml"),
        CheckConstraint("cost_ml <= cost_max", name="ck_quant_cost_ml_max"),
        CheckConstraint("cost_min <= cost_max", name="ck_quant_cost_min_max"),
        CheckConstraint("sched_min <= sched_ml", name="ck_quant_sched_min_ml"),
        CheckConstraint("sched_ml <= sched_max", name="ck_quant_sched_ml_max"),
        CheckConstraint("sched_min <= sched_max", name="ck_quant_sched_min_max"),
        CheckConstraint("p_occurrence > 0 AND p_occurrence <= 1", name="ck_quant_p_occurrence"),
        CheckConstraint("cost_pert_lambda > 0", name="ck_quant_cost_lambda"),
        CheckConstraint("sched_pert_lambda > 0", name="ck_quant_sched_lambda"),
        # NULL passes, which is the point: no base recorded means "use the run's".
        CheckConstraint("cost_base_value > 0", name="ck_quant_cost_base_value"),
        Index("ix_quant_risk_scenario", "risk_id", "scenario"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    risk_id: Mapped[int] = mapped_column(ForeignKey("risk.id", ondelete="CASCADE"), index=True)
    scenario: Mapped[str] = mapped_column(
        String(20), server_default="pre_mitigation", default="pre_mitigation"
    )

    # occurrence — continuous, and not the matrix probability band
    p_occurrence: Mapped[float] = mapped_column(Float, default=1.0)
    #: True for inherent range on a base estimate rather than a discrete event. Forces
    #: p_occurrence to 1.0. AACE 57R-09 keeps these apart; without the flag a register
    #: quietly turns into an estimate.
    is_variability: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    #: What the elicited min and max mean *by default*. Load-bearing: treating a P10/P90
    #: pair as hard bounds truncates the tail contingency is there to cover. It describes
    #: how the session was run, which is why it lives on the estimate — but a dimension
    #: may override it, and does whenever the two impacts were elicited differently.
    bound_interpretation: Mapped[str] = mapped_column(
        String(20), default="absolute", server_default="absolute"
    )

    # -- cost dimension ------------------------------------------------------------
    #: Shape is per dimension: a delay capped by a contractual milestone and the unbounded
    #: cost it drags along are not the same shape, and only one of them can be a curve.
    cost_dist: Mapped[str] = mapped_column(String(20), default="none", server_default="none")
    cost_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_ml: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_pert_lambda: Mapped[float] = mapped_column(Float, default=4.0, server_default="4.0")
    #: ``[{"x": value, "p": probability}]`` for cumulative and discrete shapes. ``p`` is a
    #: cumulative probability for the former and a mass for the latter.
    cost_points: Mapped[list | None] = mapped_column(JSON, nullable=True)
    #: ``{"min"|"ml"|"max": {"text", "source", "author", "at"}}`` — why each number is what
    #: it is. Provenance is per entry so an agent-drafted justification stays visibly an
    #: agent's until a human takes it.
    cost_rationale: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cost_basis: Mapped[str] = mapped_column(
        String(20), default="absolute", server_default="absolute"
    )
    #: Override of ``bound_interpretation`` for this dimension. NULL inherits, which is
    #: what every row written before the split does and what an unremarkable session still
    #: means. Set when the cost bounds were elicited differently from the schedule's — a
    #: contract-capped delay is absolute while the cost it drags along is a P10/P90, and
    #: under one shared value that pair has no legal encoding at all.
    cost_bound_interpretation: Mapped[str | None] = mapped_column(String(20), nullable=True)
    #: The amount a ``pct_of_base`` cost is a percentage of. NULL defers to the run's own
    #: ``base_cost``. Recorded per risk because the alternative — one project-wide base —
    #: prices a package-level percentage against the whole project and is wrong by the
    #: ratio between them, silently.
    cost_base_value: Mapped[float | None] = mapped_column(Float, nullable=True)

    # -- schedule dimension, in days -----------------------------------------------
    sched_dist: Mapped[str] = mapped_column(String(20), default="none", server_default="none")
    sched_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    sched_ml: Mapped[float | None] = mapped_column(Float, nullable=True)
    sched_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    sched_pert_lambda: Mapped[float] = mapped_column(Float, default=4.0, server_default="4.0")
    sched_points: Mapped[list | None] = mapped_column(JSON, nullable=True)
    sched_rationale: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    #: Working vs calendar days. Never inferred — the two differ by roughly 40% and the
    #: error is invisible in the output.
    sched_day_basis: Mapped[str] = mapped_column(
        String(20), default="working", server_default="working"
    )
    #: Override of ``bound_interpretation`` for this dimension. See the cost twin above.
    sched_bound_interpretation: Mapped[str | None] = mapped_column(String(20), nullable=True)

    source: Mapped[str] = mapped_column(String(20), default="sme", server_default="sme")
    confidence: Mapped[str] = mapped_column(String(20), default="medium", server_default="medium")
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
    correlation_default: Mapped[float] = mapped_column(Float, default=0.5, server_default="0.5")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RiskDriverLink(Base):
    """Many-to-many between risks and drivers."""

    __tablename__ = "risk_driver_link"
    __table_args__ = (UniqueConstraint("risk_id", "driver_id", name="uq_risk_driver_link"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    risk_id: Mapped[int] = mapped_column(ForeignKey("risk.id", ondelete="CASCADE"), index=True)
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
    "cost_dist",
    "cost_min",
    "cost_ml",
    "cost_max",
    "cost_pert_lambda",
    "cost_points",
    "cost_rationale",
    "cost_basis",
    "cost_bound_interpretation",
    "cost_base_value",
    "sched_dist",
    "sched_min",
    "sched_ml",
    "sched_max",
    "sched_pert_lambda",
    "sched_points",
    "sched_rationale",
    "sched_day_basis",
    "sched_bound_interpretation",
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
