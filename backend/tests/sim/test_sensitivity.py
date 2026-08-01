"""The schedule sensitivity index, and where it disagrees with cruciality.

``test_engine.py`` already pins the criticality index and the null-versus-zero handling of
duration sensitivity. What is here is the scale-aware metric added beside them and the one
property that justifies carrying both: they rank the same network differently, and the
disagreement is the reading.
"""

from __future__ import annotations

import pytest

from app.sim import (
    ActivityInput,
    RelationshipInput,
    RiskInput,
    RiskMappingInput,
    RunConfig,
    ScheduleInput,
    SimulationRequest,
    run,
)
from tests.sim.conftest import pert


def _chain_with(durations: list[float], uncertain: list[bool]) -> ScheduleInput:
    acts = tuple(
        ActivityInput(
            activity_id=f"A{i:03d}",
            code=f"A{i:03d}",
            name=f"Activity {i}",
            duration_days=d,
            uncertainty=pert(d * 0.5, d, d * 2.0) if u else None,
        )
        for i, (d, u) in enumerate(zip(durations, uncertain), start=1)
    )
    rels = tuple(
        RelationshipInput(predecessor_id=f"A{i:03d}", successor_id=f"A{i + 1:03d}")
        for i in range(1, len(durations))
    )
    return ScheduleInput(
        calendar_id="CAL-5x8",
        activities=acts,
        relationships=rels,
        finish_activity_ids=(f"A{len(durations):03d}",),
    )


def _run(schedule: ScheduleInput, iterations: int = 3000):
    req = SimulationRequest(
        schedule=schedule, config=RunConfig(iterations=iterations, seed=19)
    )
    return {a.activity_id: a for a in run(req).result.activity_criticality}


class TestScheduleSensitivityIndex:
    def test_it_is_criticality_times_the_spread_ratio(self) -> None:
        sched = _chain_with([10, 40, 30], [True, True, True])
        req = SimulationRequest(
            schedule=sched, config=RunConfig(iterations=3000, seed=19)
        )
        result = run(req).result
        finish_sd = result.finish_day.sd
        for a in result.activity_criticality:
            expected = a.criticality_index * a.duration_sd_days / finish_sd
            # ``sd`` on the series uses ddof=1, the accumulator population moments; the
            # gap is one part in the iteration count.
            assert a.schedule_sensitivity_index == pytest.approx(expected, rel=1e-3)

    def test_a_deterministic_activity_scores_zero_not_null(self) -> None:
        rows = _run(_chain_with([10, 40, 30], [False, True, True]))
        fixed = rows["A001"]
        assert fixed.criticality_index == pytest.approx(1.0)
        assert fixed.duration_sd_days == pytest.approx(0.0)
        assert fixed.schedule_sensitivity_index == pytest.approx(0.0)
        # The correlation stays null: nothing moved, so nothing was measured. The SSI is
        # a measured zero because the spread genuinely is zero.
        assert fixed.duration_sensitivity is None

    def test_the_longer_activity_on_one_chain_carries_the_higher_index(self) -> None:
        """Everything on a chain is always critical, so only spread separates them."""
        rows = _run(_chain_with([5, 100], [True, True]))
        assert rows["A001"].criticality_index == pytest.approx(1.0)
        assert rows["A002"].criticality_index == pytest.approx(1.0)
        assert rows["A002"].schedule_sensitivity_index > (
            5 * rows["A001"].schedule_sensitivity_index
        )

    def test_it_agrees_with_cruciality_when_durations_are_independent(self) -> None:
        """On a chain of independent activities the two metrics are the same number.

        For a finish that is a sum of independent durations,
        ``rho_i = cov(d_i, F) / (sd_i sd_F) = sd_i / sd_F``, which is the spread ratio.
        So cruciality and the SSI coincide, and pinning that they do is a check of both
        computations against each other rather than against a fixture.
        """
        rows = _run(_chain_with([40, 40, 40], [True, True, True]), iterations=8000)
        for a in rows.values():
            assert a.schedule_sensitivity_index == pytest.approx(a.cruciality, rel=0.05)

    def test_a_shared_driver_pulls_them_apart(self) -> None:
        """One risk driving two activities is where the two metrics stop agreeing.

        The same sampled delay lands on both driven activities, so it reaches the finish
        twice. Correlation sees the shared cause's whole effect and reports it against
        each activity; the spread ratio sees only what that activity itself contributes.
        Cruciality therefore comes out at twice the SSI on each driven activity, and at
        the SSI on the one that varies alone. Neither is wrong — they answer different
        questions, which is why both are carried.
        """
        acts = (
            ActivityInput(activity_id="A001", code="A001", duration_days=10.0),
            ActivityInput(activity_id="A002", code="A002", duration_days=10.0),
            ActivityInput(
                activity_id="A003",
                code="A003",
                duration_days=40.0,
                uncertainty=pert(20.0, 40.0, 90.0),
            ),
        )
        rels = (
            RelationshipInput(predecessor_id="A001", successor_id="A002"),
            RelationshipInput(predecessor_id="A002", successor_id="A003"),
        )
        req = SimulationRequest(
            risks=(
                RiskInput(
                    risk_id=1,
                    code="SCH-DRV-0001",
                    sched=pert(10.0, 20.0, 40.0),
                    mappings=(
                        RiskMappingInput(
                            mapping_type="duration_driver",
                            activity_ids=("A001", "A002"),
                        ),
                    ),
                ),
            ),
            schedule=ScheduleInput(
                calendar_id="CAL-5x8",
                activities=acts,
                relationships=rels,
                finish_activity_ids=("A003",),
            ),
            config=RunConfig(iterations=8000, seed=23),
        )
        rows = {a.activity_id: a for a in run(req).result.activity_criticality}
        driven = rows["A001"]
        alone = rows["A003"]
        assert driven.cruciality == pytest.approx(
            2.0 * driven.schedule_sensitivity_index, rel=0.05
        )
        assert alone.cruciality == pytest.approx(
            alone.schedule_sensitivity_index, rel=0.05
        )


class TestRetention:
    def test_truncation_keeps_the_top_of_both_rankings(self) -> None:
        sched = _chain_with([5] + [20] * 9, [True] * 10)
        req = SimulationRequest(
            schedule=sched,
            config=RunConfig(
                iterations=1000, seed=4, max_sensitivity_activities=10
            ),
        )
        rows = run(req).result.activity_criticality
        # Nothing to truncate here; the guard is that the cap is not applied early.
        assert len(rows) == 10

        req = SimulationRequest(
            schedule=sched,
            config=RunConfig(iterations=1000, seed=4, max_sensitivity_activities=10)
            .model_copy(update={"max_sensitivity_activities": 4}),
        )
        kept = run(req).result.activity_criticality
        assert len(kept) == 4
        by_ssi = sorted(rows, key=lambda r: -r.schedule_sensitivity_index)[:4]
        by_cruc = sorted(rows, key=lambda r: -r.cruciality)[:4]
        kept_ids = {r.activity_id for r in kept}
        # The retained set is drawn from the union, so the leader of each ranking
        # survives even where the two disagree.
        assert by_ssi[0].activity_id in kept_ids
        assert by_cruc[0].activity_id in kept_ids

    def test_truncation_says_it_happened(self) -> None:
        sched = _chain_with([20] * 8, [True] * 8)
        req = SimulationRequest(
            schedule=sched,
            config=RunConfig(iterations=1000, seed=4).model_copy(
                update={"max_sensitivity_activities": 3}
            ),
        )
        result = run(req).result
        assert len(result.activity_criticality) == 3
        assert any("truncated" in w for w in result.warnings)
