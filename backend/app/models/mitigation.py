"""Mitigation: the actions, what they cost, and the residual position they claim.

Three tables, and the split is the whole design.

``MitigationAction`` is unchanged from the qualitative register except for two columns:
``plan_id``, so actions can be grouped into the package they belong to, and
``sched_days``, because a mitigation that takes four weeks of somebody's programme is not
free just because its budget line is. Actions may still sit outside any plan — that is
what every action written before this module existed does.

``MitigationPlan`` is the package: a named set of actions with a status and a cost. Cost
is deterministic and additive, unlike contingency, and the two are never summed into one
number (invariant 1). A plan's cost belongs beside a contingency figure, never inside it.

``MitigationPlanRisk`` is the residual *declaration*: what an analyst says this package
leaves behind, per risk. It is deliberately not an effectiveness score. Effectiveness is
measured by re-simulating the post-mitigation register and reading the delta (4.5) — a
declared residual is an input to that measurement, never a substitute for it. Nothing in
this module claims a benefit; it only says what to simulate.

The declaration is a small set of factors rather than a second copy of the estimate
schema, and materialisation projects it into ``RiskQuantEstimate`` rows carrying
``scenario="post_mitigation"``. That column, its unique constraint, and
``sim_assembly.assemble(scenario=...)`` were all built for this: the post-mitigation run
needs no new engine path, no new assembly branch, and no new fingerprint. Once
materialised the residual is an ordinary estimate and the analyst can edit it through the
ordinary elicitation screens, which is the point — the factors are a fast way to draft a
residual register, not a cage around it.
"""

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

#: Plan lifecycle. ``superseded`` exists so a plan that has been replaced can stay in the
#: record: it was materialised once, a run quoted it, and deleting it would strand that.
PLAN_STATUSES = ("draft", "proposed", "approved", "rejected", "superseded")

#: What the plan does to a risk.
#:
#: ``accept`` is not the same as having no entry, even though both carry the estimate
#: through unchanged. One is a decision and the other is an omission, and a residual
#: register cannot tell a reviewer which is which unless the decision is written down.
TREATMENTS = ("reduce", "retire", "accept")

#: How a ``reduce`` is expressed. Factors scale what was elicited; absolute replaces it.
TREATMENT_MODES = ("factor", "absolute")


class MitigationPlan(Base):
    """A named package of mitigation actions and the residual register it claims."""

    __tablename__ = "mitigation_plan"
    __table_args__ = (
        UniqueConstraint("scope_id", "name", name="uq_mitigation_plan_scope_name"),
        CheckConstraint(
            "status IN ('draft', 'proposed', 'approved', 'rejected', 'superseded')",
            name="ck_mitigation_plan_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    #: The project this plan treats. ``RESTRICT`` matches ``risk`` and ``simulation_run``:
    #: a scope that still owns a plan is not deletable.
    scope_id: Mapped[int] = mapped_column(
        ForeignKey("scope_node.id", ondelete="RESTRICT"), index=True
    )

    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default="draft", server_default="draft", index=True
    )

    # -- the materialisation record ------------------------------------------------
    #: When this plan was last projected into ``post_mitigation`` estimates, by whom, and
    #: a fingerprint of exactly what it wrote. The fingerprint is what lets a later run be
    #: attributed to a plan: if the post-mitigation register no longer hashes to this,
    #: something was edited afterwards and the run is not measuring this package.
    materialized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    materialized_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    materialized_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    materialized_risk_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    materialized_retired_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_by: Mapped[str] = mapped_column(
        String(120), default="Unknown", server_default="Unknown"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MitigationPlanRisk(Base):
    """What one plan claims it leaves behind on one risk.

    Factors are bounded at 1.0 on purpose. A treatment that makes a risk *worse* is a
    secondary risk and belongs in the register as its own line with its own cause — hiding
    it inside a factor above one would leave a residual register nobody could trace back
    to a decision. The lower bound is exclusive because ``risk_quant_estimate`` requires a
    positive occurrence probability; eliminating a risk entirely is ``retire``, which
    writes no residual row at all rather than one with a probability of zero.
    """

    __tablename__ = "mitigation_plan_risk"
    __table_args__ = (
        UniqueConstraint("plan_id", "risk_id", name="uq_mitigation_plan_risk"),
        CheckConstraint(
            "treatment IN ('reduce', 'retire', 'accept')", name="ck_plan_risk_treatment"
        ),
        CheckConstraint("mode IN ('factor', 'absolute')", name="ck_plan_risk_mode"),
        CheckConstraint("p_factor > 0 AND p_factor <= 1", name="ck_plan_risk_p_factor"),
        CheckConstraint(
            "cost_factor > 0 AND cost_factor <= 1", name="ck_plan_risk_cost_factor"
        ),
        CheckConstraint(
            "sched_factor > 0 AND sched_factor <= 1", name="ck_plan_risk_sched_factor"
        ),
        CheckConstraint(
            "residual_p IS NULL OR (residual_p > 0 AND residual_p <= 1)",
            name="ck_plan_risk_residual_p",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("mitigation_plan.id", ondelete="CASCADE"), index=True
    )
    risk_id: Mapped[int] = mapped_column(
        ForeignKey("risk.id", ondelete="CASCADE"), index=True
    )

    treatment: Mapped[str] = mapped_column(
        String(20), default="reduce", server_default="reduce"
    )
    mode: Mapped[str] = mapped_column(String(20), default="factor", server_default="factor")

    #: Multipliers on the elicited pre-mitigation numbers. 1.0 leaves a dimension alone,
    #: which is how "this action shortens the delay but does not touch the cost" is said.
    p_factor: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0")
    cost_factor: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0")
    sched_factor: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0")

    #: ``mode="absolute"``: the residual numbers outright. Any left NULL falls back to the
    #: pre-mitigation value, so an analyst who only knows the new maximum can say just
    #: that. Shape, lambda and basis always carry over from the pre-mitigation estimate —
    #: a mitigation changes magnitude and likelihood, and an analyst who also wants a
    #: different distribution family edits the materialised estimate directly.
    residual_p: Mapped[float | None] = mapped_column(Float, nullable=True)
    residual_cost_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    residual_cost_ml: Mapped[float | None] = mapped_column(Float, nullable=True)
    residual_cost_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    residual_sched_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    residual_sched_ml: Mapped[float | None] = mapped_column(Float, nullable=True)
    residual_sched_max: Mapped[float | None] = mapped_column(Float, nullable=True)

    #: Why. A residual with no rationale is a number somebody will have to defend later
    #: with nothing to defend it from.
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MitigationAction(Base):
    """A single mitigation action belonging to a risk."""

    __tablename__ = "mitigation_action"

    id: Mapped[int] = mapped_column(primary_key=True)
    risk_id: Mapped[int] = mapped_column(
        ForeignKey("risk.id", ondelete="CASCADE"), index=True
    )
    #: The package this action belongs to, if any. ``SET NULL``: deleting a plan is a
    #: decision about the package, not about the actions people have been doing, and an
    #: action that quietly disappeared with it would take its history with it.
    plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("mitigation_plan.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(Text, default="")
    owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    budget: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Programme the action itself consumes. Separate from the delay the risk causes: a
    #: pre-order that removes twenty days of exposure but occupies ten days of float has
    #: bought ten, and a cost rollup that only counts money says it bought twenty.
    sched_days: Mapped[float | None] = mapped_column(Float, nullable=True)
    completion_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    effectiveness: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(
        String(30), default="Proposed", server_default="Proposed"
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


ACTION_FIELDS = [
    "action",
    "owner",
    "due_date",
    "budget",
    "sched_days",
    "completion_pct",
    "effectiveness",
    "status",
    "plan_id",
]


def action_snapshot(a: MitigationAction) -> dict:
    out: dict = {}
    for f in ACTION_FIELDS:
        v = getattr(a, f, None)
        if isinstance(v, date):
            v = v.isoformat()
        out[f] = v
    return out


def action_diff(old: dict, new: dict) -> list[dict]:
    changes: list[dict] = []
    for f in ACTION_FIELDS:
        if old.get(f) != new.get(f):
            changes.append({"field": f, "old": old.get(f), "new": new.get(f)})
    return changes


PLAN_RISK_FIELDS = [
    "treatment",
    "mode",
    "p_factor",
    "cost_factor",
    "sched_factor",
    "residual_p",
    "residual_cost_min",
    "residual_cost_ml",
    "residual_cost_max",
    "residual_sched_min",
    "residual_sched_ml",
    "residual_sched_max",
    "rationale",
]


def plan_risk_snapshot(e: MitigationPlanRisk) -> dict:
    return {f: getattr(e, f, None) for f in PLAN_RISK_FIELDS}


def plan_risk_diff(old: dict, new: dict) -> list[dict]:
    return [
        {"field": f, "old": old.get(f), "new": new.get(f)}
        for f in PLAN_RISK_FIELDS
        if old.get(f) != new.get(f)
    ]
