"""Simulation run persistence.

One row per run, append-only (invariant 5). A run is never edited and never deleted
through the API: it is the record of what was asked, what came back, and who asked. Re-
running writes a new row, exactly as a re-parse writes a new schedule version.

The one narrow exception is cancellation. A run still sitting in ``queued`` has no result
to protect — nothing has come back yet — so a human may move it straight to ``cancelled``
via ``POST /simulations/{id}/cancel`` rather than leaving it stuck behind a worker that
will never claim it. ``cancelled_by``/``cancelled_at`` record who and when, the same way
every other decision on this row is attributed. Once a run leaves ``queued`` — running or
terminal — it is exactly as immutable as this docstring always said; the cancel route
refuses anything that isn't still ``queued``.

What is stored, and why it is split the way it is:

``request_json`` holds the :class:`~app.sim.inputs.SimulationRequest` **minus the
schedule**, and ``schedule_version_id`` points at the network instead. That is not a size
optimisation dressed up as a principle — ``schedule_version`` is append-only and its
activities are never mutated, so the reference is exactly as precise as an inlined copy
would be, and inlining five thousand activities into every run row duplicates megabytes
for nothing. ``inputs_sha256`` is still the fingerprint of the *whole* request, schedule
included, so the shortcut cannot hide a difference: the worker rebuilds the schedule from
the version, re-fingerprints, and refuses to run if the digest moved.

``result_json`` is :class:`~app.sim.engine.SimulationResult` serialised whole. It already
carries percentiles, the S-curve grid and histogram bins per series, so there is nothing
to reshape on the way in and nothing to recompute on the way out. Per-iteration arrays are
deliberately not stored: ``RunArrays`` exists for a caller that wants to re-cut a run, and
re-cutting is what a replay is for.

Both payloads are deferred. The list endpoint reads a dozen scalar columns and must not
drag a megabyte of JSON per row behind it.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

#: Lifecycle. ``queued`` is written by the API; ``running``/``succeeded``/``failed`` by
#: the worker; ``cancelled`` by the API, and only starting from ``queued``.
RUN_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")

#: Terminal states. Nothing transitions out of these.
TERMINAL_STATUSES = ("succeeded", "failed", "cancelled")


class SimulationRun(Base):
    """One Monte Carlo run: its inputs, its result, and who is answerable for it."""

    __tablename__ = "simulation_run"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_simrun_status",
        ),
        CheckConstraint("iterations >= 100", name="ck_simrun_iterations"),
        CheckConstraint("base_cost >= 0", name="ck_simrun_base_cost"),
        CheckConstraint("burn_rate_per_day >= 0", name="ck_simrun_burn_rate"),
        Index("ix_simrun_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    #: The project this run was computed for. A program-level rollup is a different kind
    #: of run and will carry its own child-run references (P8); this column always names a
    #: project.
    scope_id: Mapped[int] = mapped_column(
        ForeignKey("scope_node.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(200), default="", server_default="")
    status: Mapped[str] = mapped_column(
        String(20), default="queued", server_default="queued", index=True
    )

    #: Which set of estimates was simulated. Post-mitigation runs are how mitigation ROI
    #: gets measured later; the column exists now so the two can never be confused.
    scenario: Mapped[str] = mapped_column(
        String(20), default="pre_mitigation", server_default="pre_mitigation"
    )

    #: ``SET NULL`` rather than ``CASCADE``: deleting a schedule must not erase the
    #: record that a run happened against it. The run stops being replayable at that
    #: point, which ``schedule_version_id IS NULL`` says plainly.
    schedule_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("schedule_version.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # -- the quality gate (invariant 3) --------------------------------------------
    #: The gate run this was cleared against. Null when no schedule was simulated.
    dcma_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gate_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    #: A human decided to simulate a schedule the gate failed. Recorded on the run, not
    #: in a log line, because the override is a property of the number that came out.
    gate_override: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    gate_override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- run configuration, mirrored into columns so a list view can sort on it -----
    iterations: Mapped[int] = mapped_column(Integer, default=10_000)
    seed: Mapped[int] = mapped_column(Integer, default=12345)
    sampling: Mapped[str] = mapped_column(String(10), default="lhs", server_default="lhs")
    base_cost: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    burn_rate_per_day: Mapped[float] = mapped_column(
        Float, default=0.0, server_default="0"
    )

    # -- what went in --------------------------------------------------------------
    risk_count: Mapped[int] = mapped_column(Integer, default=0)
    #: Risks carrying at least one accepted activity mapping *and* a schedule impact.
    mapped_risk_count: Mapped[int] = mapped_column(Integer, default=0)
    activity_count: Mapped[int] = mapped_column(Integer, default=0)
    #: Risks with an estimate that could not be sampled, and why. Reported rather than
    #: silently dropped: a contingency computed over a subset of the register without
    #: saying so is the most expensive kind of wrong.
    excluded: Mapped[list | None] = mapped_column(JSON, nullable=True)
    #: Assembly-time findings — calendar days converted, mappings without an estimate,
    #: relationships pointing outside the parse.
    assembly_notes: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # -- the reproducibility record (invariant 6) ----------------------------------
    engine_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    #: Resolved by the engine from the network size, not requested. Replaying a run means
    #: replaying its chunking.
    chunk_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Fingerprint of the whole request including the schedule.
    inputs_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    request_json: Mapped[dict | None] = mapped_column(JSON, nullable=True, deferred=True)
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True, deferred=True)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: Set only by a cancel out of ``queued`` — never by the worker. Null on every other
    #: run, including one that failed or succeeded. Kept apart from ``finished_at``/
    #: ``error``, which mean "the engine reached a terminal state" and were never true for
    #: a run the worker never touched.
    cancelled_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_by: Mapped[str] = mapped_column(String(120), default="Unknown")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
