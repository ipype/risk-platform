"""Fixtures for the simulation suite.

Deliberately no database and no async: ``app.sim`` has neither, and a test that reached for
either would be testing the adapter that does not exist yet rather than the engine.
"""

from __future__ import annotations

import pytest

from app.sim import (
    ActivityInput,
    CorrelationInput,
    DistributionSpec,
    DriverSpec,
    RelationshipInput,
    RiskInput,
    RiskMappingInput,
    RunConfig,
    ScheduleInput,
    SimulationRequest,
)


def pert(lo: float, ml: float, hi: float, lam: float = 4.0) -> DistributionSpec:
    """A Beta-PERT with shape parameters derived the way ``pert_moments`` derives them."""
    span = hi - lo
    if span <= 0:
        return DistributionSpec(kind="point", lo=lo, hi=lo)
    return DistributionSpec(
        kind="pert",
        lo=lo,
        ml=ml,
        hi=hi,
        alpha=1.0 + lam * (ml - lo) / span,
        beta=1.0 + lam * (hi - ml) / span,
    )


def tri(lo: float, ml: float, hi: float) -> DistributionSpec:
    return DistributionSpec(kind="triangular", lo=lo, ml=ml, hi=hi)


def chain(durations: list[float], *, uncertain: bool = True) -> ScheduleInput:
    """A straight finish-to-start chain, which makes the arithmetic checkable by hand."""
    acts = tuple(
        ActivityInput(
            activity_id=f"A{i:03d}",
            code=f"A{i:03d}",
            name=f"Activity {i}",
            duration_days=d,
            uncertainty=pert(d * 0.9, d, d * 1.6) if uncertain and d > 0 else None,
        )
        for i, d in enumerate(durations, start=1)
    )
    rels = tuple(
        RelationshipInput(
            predecessor_id=f"A{i:03d}", successor_id=f"A{i + 1:03d}", type="FS"
        )
        for i in range(1, len(durations))
    )
    return ScheduleInput(
        calendar_id="CAL-5x8",
        activities=acts,
        relationships=rels,
        finish_activity_ids=(f"A{len(durations):03d}",),
    )


@pytest.fixture
def simple_request() -> SimulationRequest:
    """A small integrated run: two mapped risks, one pure cost risk, a five-link chain."""
    return SimulationRequest(
        risks=(
            RiskInput(
                risk_id=1,
                code="EXT-WEA-0001",
                title="Winter shutdown",
                p_occurrence=0.4,
                cost=pert(100_000, 300_000, 900_000),
                sched=tri(5, 15, 40),
                drivers=("weather",),
                mappings=(
                    RiskMappingInput(
                        mapping_type="duration_driver",
                        activity_ids=("A002", "A003"),
                    ),
                ),
            ),
            RiskInput(
                risk_id=2,
                code="CON-PRD-0002",
                title="Productivity shortfall",
                p_occurrence=0.6,
                cost=pert(50_000, 250_000, 1_200_000),
                sched=pert(2, 8, 30),
                drivers=("labour",),
                mappings=(
                    RiskMappingInput(
                        mapping_type="inserted_activity",
                        predecessor_id="A003",
                        successor_id="A004",
                    ),
                ),
            ),
            RiskInput(
                risk_id=3,
                code="COM-ESC-0003",
                title="Steel escalation",
                is_variability=True,
                cost=DistributionSpec(kind="uniform", lo=-200_000, hi=800_000),
                drivers=("escalation",),
            ),
        ),
        schedule=chain([10, 40, 30, 60, 20]),
        correlation=CorrelationInput(
            drivers=(
                DriverSpec(name="weather", coefficient=0.6),
                DriverSpec(name="labour", coefficient=0.5),
                DriverSpec(name="escalation", coefficient=0.4),
            )
        ),
        config=RunConfig(
            iterations=4000,
            seed=7,
            base_cost=25_000_000,
            burn_rate_per_day=45_000,
        ),
    )
