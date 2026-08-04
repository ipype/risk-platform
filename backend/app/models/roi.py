"""The record that two runs were a matched pair, and what the package cost at the time.

The comparison itself is *not* stored. Two simulation runs are immutable (invariant 5), so
subtracting them is a pure function of rows that cannot change, and a stored copy of the
answer could only ever drift from the code that produces it. What cannot be recovered
afterwards is the pairing: that *these* two runs were started together to measure *that*
package. Nothing in ``simulation_run`` says so, and reconstructing it from timestamps is
guesswork dressed as provenance.

Two snapshots are stored alongside the pairing, and both exist because the thing they
describe is editable while the runs are not:

``plan_budget`` / ``plan_sched_days`` / ``plan_unpriced_count`` freeze what the package
cost when the pair was made. Somebody re-costing an action next month must not silently
change a benefit-cost ratio that has already been quoted in a report; the current cost is
still readable from the plan, and the API reports both so a divergence is visible rather
than invisible.

``plan_fingerprint`` freezes ``mitigation_plan.materialized_fingerprint`` at pairing. If
the plan is re-materialised later, or a residual is hand-edited and the plan re-written,
this stops matching and the comparison is flagged stale — still valid as a record of what
was run, no longer a description of the package as it now stands. That question is exactly
what the fingerprint was added to 0015 to answer.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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


class MitigationRoi(Base):
    """One baseline run, one treated run, and the package they were run to measure."""

    __tablename__ = "mitigation_roi"
    __table_args__ = (
        # The same two runs paired twice against the same plan is a duplicate, not a
        # second opinion: the comparison is a pure function of the pair.
        UniqueConstraint(
            "plan_id", "before_run_id", "after_run_id", name="uq_roi_plan_runs"
        ),
        CheckConstraint("before_run_id <> after_run_id", name="ck_roi_distinct_runs"),
        CheckConstraint("percentile > 0 AND percentile < 100", name="ck_roi_percentile"),
        CheckConstraint("plan_budget >= 0", name="ck_roi_plan_budget"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    #: ``RESTRICT`` throughout. A plan that has been measured is not deletable, and
    #: neither is a run that a measurement quotes — deleting either would leave a
    #: comparison that cannot say what it compared.
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("mitigation_plan.id", ondelete="RESTRICT"), index=True
    )
    scope_id: Mapped[int] = mapped_column(
        ForeignKey("scope_node.id", ondelete="RESTRICT"), index=True
    )
    before_run_id: Mapped[int] = mapped_column(
        ForeignKey("simulation_run.id", ondelete="RESTRICT"), index=True
    )
    after_run_id: Mapped[int] = mapped_column(
        ForeignKey("simulation_run.id", ondelete="RESTRICT"), index=True
    )

    name: Mapped[str] = mapped_column(String(200), default="", server_default="")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: The percentile the headline is quoted at. Stored rather than assumed because a
    #: comparison read at P50 and one read at P90 are different claims about the same
    #: pair, and a report has to say which it made.
    percentile: Mapped[float] = mapped_column(Float, default=80.0, server_default="80")

    #: Both runs were started from one request with one seed. False when an analyst paired
    #: two runs that already existed, which is allowed and is worth saying out loud: the
    #: difference then carries sampling noise a matched pair would have cancelled.
    seed_shared: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )

    plan_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    plan_budget: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    plan_sched_days: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    plan_unpriced_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )

    created_by: Mapped[str] = mapped_column(
        String(120), default="Unknown", server_default="Unknown"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
