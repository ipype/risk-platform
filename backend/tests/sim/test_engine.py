"""Engine mechanics: what each mapping type does, and what the run refuses to do."""

from __future__ import annotations

import numpy as np
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
    run,
)
from app.sim.errors import RunTooLarge, SimulationInputInvalid

from .conftest import chain, pert, tri


def p_of(points, p: float) -> float:
    return next(x.value for x in points if x.p == p)


class TestCostOnly:
    def test_runs_without_a_schedule(self) -> None:
        req = SimulationRequest(
            risks=(
                RiskInput(
                    risk_id=1, code="A", p_occurrence=0.5, cost=pert(0, 100, 400)
                ),
            ),
            config=RunConfig(iterations=2000, seed=1, base_cost=1_000.0),
        )
        r = run(req).result
        assert r.delay_days is None and r.finish_day is None
        assert r.total_cost.mean == pytest.approx(1_000.0 + r.risk_cost.mean)
        assert r.deterministic.baseline_finish_day is None

    def test_occurrence_gates_the_impact(self) -> None:
        req = SimulationRequest(
            risks=(
                RiskInput(
                    risk_id=1, code="A", p_occurrence=0.25, cost=pert(100, 200, 300)
                ),
            ),
            config=RunConfig(iterations=8000, seed=3),
        )
        out = run(req)
        col = out.arrays.contributions[:, 0]
        assert (col == 0.0).mean() == pytest.approx(0.75, abs=0.02)
        assert out.result.risk_sensitivity[0].realised_frequency == pytest.approx(
            0.25, abs=0.02
        )

    def test_pct_of_base_scales_against_the_reference(self) -> None:
        req = SimulationRequest(
            risks=(
                RiskInput(
                    risk_id=1,
                    code="A",
                    cost=DistributionSpec(kind="point", lo=10.0, hi=10.0),
                    cost_basis="pct_of_base",
                    cost_base_reference=2_000_000.0,
                ),
            ),
            config=RunConfig(iterations=500, seed=1),
        )
        assert run(req).result.risk_cost.mean == pytest.approx(200_000.0)

    def test_warns_when_a_percentage_has_nothing_to_apply_to(self) -> None:
        req = SimulationRequest(
            risks=(
                RiskInput(
                    risk_id=1,
                    code="A",
                    cost=DistributionSpec(kind="point", lo=10.0, hi=10.0),
                    cost_basis="pct_of_base",
                ),
            ),
            config=RunConfig(iterations=200, seed=1),
        )
        assert any(
            "percentage of a base of zero" in w for w in run(req).result.warnings
        )


class TestDriverSemantics:
    def _driver_run(self, activity_ids: tuple[str, ...]) -> float:
        req = SimulationRequest(
            risks=(
                RiskInput(
                    risk_id=1,
                    code="D",
                    p_occurrence=1.0,
                    sched=DistributionSpec(kind="point", lo=10.0, hi=10.0),
                    mappings=(
                        RiskMappingInput(
                            mapping_type="duration_driver", activity_ids=activity_ids
                        ),
                    ),
                ),
            ),
            schedule=chain([20, 20, 20], uncertain=False),
            config=RunConfig(iterations=200, seed=1),
        )
        return run(req).result.delay_days.mean

    def test_a_driver_adds_its_delay_to_every_driven_activity(self) -> None:
        # Not divided among them. Three sequential activities driven by one 10-day risk
        # is 30 days of delay, which is the Hulett semantic and why the API refuses
        # allocation_pct on this mapping type.
        assert self._driver_run(("A001",)) == pytest.approx(10.0)
        assert self._driver_run(("A001", "A002")) == pytest.approx(20.0)
        assert self._driver_run(("A001", "A002", "A003")) == pytest.approx(30.0)

    def test_an_inserted_activity_splits_its_allocation(self) -> None:
        req = SimulationRequest(
            risks=(
                RiskInput(
                    risk_id=1,
                    code="I",
                    sched=DistributionSpec(kind="point", lo=30.0, hi=30.0),
                    mappings=(
                        RiskMappingInput(
                            mapping_type="inserted_activity",
                            predecessor_id="A001",
                            successor_id="A002",
                            allocation_pct=40.0,
                        ),
                    ),
                ),
            ),
            schedule=chain([20, 20], uncertain=False),
            config=RunConfig(iterations=200, seed=1),
        )
        r = run(req).result
        assert r.deterministic.inserted_activities == 1
        assert r.delay_days.mean == pytest.approx(12.0)

    def test_unset_allocation_splits_evenly_across_insertion_points(self) -> None:
        req = SimulationRequest(
            risks=(
                RiskInput(
                    risk_id=1,
                    code="I",
                    sched=DistributionSpec(kind="point", lo=30.0, hi=30.0),
                    mappings=(
                        RiskMappingInput(
                            mapping_type="inserted_activity",
                            predecessor_id="A001",
                            successor_id="A002",
                        ),
                        RiskMappingInput(
                            mapping_type="inserted_activity",
                            predecessor_id="A002",
                            successor_id="A003",
                        ),
                    ),
                ),
            ),
            schedule=chain([20, 20, 20], uncertain=False),
            config=RunConfig(iterations=200, seed=1),
        )
        # 30 days over two insertion points is 15 at each, so 30 in total on one chain.
        assert run(req).result.delay_days.mean == pytest.approx(30.0)

    def test_a_missed_risk_inserts_nothing(self) -> None:
        req = SimulationRequest(
            risks=(
                RiskInput(
                    risk_id=1,
                    code="I",
                    p_occurrence=0.5,
                    sched=DistributionSpec(kind="point", lo=40.0, hi=40.0),
                    mappings=(
                        RiskMappingInput(
                            mapping_type="inserted_activity",
                            predecessor_id="A001",
                            successor_id="A002",
                        ),
                    ),
                ),
            ),
            schedule=chain([20, 20], uncertain=False),
            config=RunConfig(iterations=4000, seed=2),
        )
        delay = run(req).arrays.delay_days
        assert set(np.unique(np.round(delay, 9))) == {0.0, 40.0}
        assert (delay == 0.0).mean() == pytest.approx(0.5, abs=0.03)

    def test_float_absorbs_delay_instead_of_passing_it_through(self) -> None:
        # The schedule-side twin of adding percentiles. A ten-day risk on an activity
        # with thirty days of float moves nothing.
        req = SimulationRequest(
            risks=(
                RiskInput(
                    risk_id=1,
                    code="D",
                    sched=DistributionSpec(kind="point", lo=10.0, hi=10.0),
                    mappings=(
                        RiskMappingInput(
                            mapping_type="duration_driver", activity_ids=("SHORT",)
                        ),
                    ),
                ),
            ),
            schedule=ScheduleInput(
                calendar_id="C",
                activities=(
                    ActivityInput(activity_id="START", duration_days=0.0),
                    ActivityInput(activity_id="LONG", duration_days=100.0),
                    ActivityInput(activity_id="SHORT", duration_days=10.0),
                    ActivityInput(activity_id="END", duration_days=0.0),
                ),
                relationships=(
                    RelationshipInput(predecessor_id="START", successor_id="LONG"),
                    RelationshipInput(predecessor_id="START", successor_id="SHORT"),
                    RelationshipInput(predecessor_id="LONG", successor_id="END"),
                    RelationshipInput(predecessor_id="SHORT", successor_id="END"),
                ),
                finish_activity_ids=("END",),
            ),
            config=RunConfig(iterations=200, seed=1),
        )
        assert run(req).result.delay_days.mean == pytest.approx(0.0)


class TestCriticality:
    def test_index_reports_how_often_an_activity_drove_the_finish(self) -> None:
        req = SimulationRequest(
            schedule=ScheduleInput(
                calendar_id="C",
                activities=(
                    ActivityInput(activity_id="START", duration_days=0.0),
                    ActivityInput(
                        activity_id="P1",
                        duration_days=50.0,
                        uncertainty=DistributionSpec(kind="uniform", lo=40.0, hi=60.0),
                    ),
                    ActivityInput(
                        activity_id="P2",
                        duration_days=50.0,
                        uncertainty=DistributionSpec(kind="uniform", lo=40.0, hi=60.0),
                    ),
                    ActivityInput(activity_id="END", duration_days=0.0),
                ),
                relationships=(
                    RelationshipInput(predecessor_id="START", successor_id="P1"),
                    RelationshipInput(predecessor_id="START", successor_id="P2"),
                    RelationshipInput(predecessor_id="P1", successor_id="END"),
                    RelationshipInput(predecessor_id="P2", successor_id="END"),
                ),
                finish_activity_ids=("END",),
            ),
            config=RunConfig(iterations=4000, seed=4),
        )
        by_id = {a.activity_id: a for a in run(req).result.activity_criticality}
        # Two identical parallel paths: each drives the finish about half the time.
        assert by_id["P1"].criticality_index == pytest.approx(0.5, abs=0.05)
        assert by_id["P2"].criticality_index == pytest.approx(0.5, abs=0.05)
        assert by_id["END"].criticality_index == pytest.approx(1.0)
        assert by_id["P1"].duration_sensitivity > 0.5

    def test_a_fixed_duration_reports_no_sensitivity_rather_than_zero(self) -> None:
        req = SimulationRequest(
            schedule=chain([10, 20], uncertain=False),
            config=RunConfig(iterations=200, seed=1),
        )
        rows = run(req).result.activity_criticality
        assert all(a.duration_sensitivity is None for a in rows)
        assert all(a.cruciality == 0.0 for a in rows)


class TestWarnings:
    def test_flags_a_schedule_with_no_background_uncertainty(self) -> None:
        req = SimulationRequest(
            schedule=chain([10, 20], uncertain=False),
            config=RunConfig(iterations=200, seed=1),
        )
        assert any(
            "No activity carries duration uncertainty" in w
            for w in run(req).result.warnings
        )

    def test_flags_a_hard_constraint_it_cannot_honour(self) -> None:
        req = SimulationRequest(
            schedule=ScheduleInput(
                calendar_id="C",
                activities=(
                    ActivityInput(
                        activity_id="A", duration_days=10.0, has_hard_constraint=True
                    ),
                ),
            ),
            config=RunConfig(iterations=200, seed=1),
        )
        assert any("hard date constraint" in w for w in run(req).result.warnings)

    def test_flags_a_risk_too_rare_to_sample(self) -> None:
        req = SimulationRequest(
            risks=(
                RiskInput(
                    risk_id=1, code="RARE", p_occurrence=0.001, cost=pert(1e6, 5e6, 2e7)
                ),
            ),
            config=RunConfig(iterations=1000, seed=1),
        )
        assert any("thinly sampled" in w for w in run(req).result.warnings)

    def test_flags_a_register_with_nothing_to_simulate(self) -> None:
        req = SimulationRequest(
            risks=(RiskInput(risk_id=1, code="EMPTY"),),
            config=RunConfig(iterations=200, seed=1),
        )
        assert any("nothing to simulate" in w for w in run(req).result.warnings)


class TestValidation:
    def test_a_schedule_impact_with_no_mapping_is_refused(self) -> None:
        with pytest.raises(Exception, match="nowhere to land"):
            RiskInput(risk_id=1, code="X", sched=tri(1, 2, 3))

    def test_a_mapping_with_no_schedule_impact_is_refused(self) -> None:
        with pytest.raises(Exception, match="contribute nothing"):
            RiskInput(
                risk_id=1,
                code="X",
                cost=pert(1, 2, 3),
                mappings=(
                    RiskMappingInput(
                        mapping_type="duration_driver", activity_ids=("A001",)
                    ),
                ),
            )

    def test_allocation_on_a_driver_is_refused(self) -> None:
        with pytest.raises(Exception, match="does not apply"):
            RiskMappingInput(
                mapping_type="duration_driver",
                activity_ids=("A001",),
                allocation_pct=50.0,
            )

    def test_a_variability_row_must_be_certain(self) -> None:
        with pytest.raises(Exception, match="certain by definition"):
            RiskInput(
                risk_id=1,
                code="V",
                is_variability=True,
                p_occurrence=0.5,
                cost=pert(1, 2, 3),
            )

    def test_mapping_to_an_unknown_activity_is_refused(self) -> None:
        with pytest.raises(SimulationInputInvalid, match="unknown activity"):
            SimulationRequest(
                risks=(
                    RiskInput(
                        risk_id=1,
                        code="X",
                        sched=tri(1, 2, 3),
                        mappings=(
                            RiskMappingInput(
                                mapping_type="duration_driver",
                                activity_ids=("NOPE",),
                            ),
                        ),
                    ),
                ),
                schedule=chain([10]),
            )

    def test_an_undeclared_driver_is_refused(self) -> None:
        with pytest.raises(SimulationInputInvalid, match="undeclared driver"):
            SimulationRequest(
                risks=(
                    RiskInput(
                        risk_id=1, code="X", cost=pert(1, 2, 3), drivers=("ghost",)
                    ),
                ),
                correlation=CorrelationInput(
                    drivers=(DriverSpec(name="weather", coefficient=0.5),)
                ),
            )

    def test_a_burn_rate_with_no_schedule_is_refused(self) -> None:
        with pytest.raises(SimulationInputInvalid, match="no schedule"):
            SimulationRequest(config=RunConfig(burn_rate_per_day=1000.0))

    def test_duplicate_risk_ids_are_refused(self) -> None:
        with pytest.raises(SimulationInputInvalid, match="not unique"):
            SimulationRequest(
                risks=(
                    RiskInput(risk_id=1, code="A", cost=pert(1, 2, 3)),
                    RiskInput(risk_id=1, code="B", cost=pert(1, 2, 3)),
                )
            )

    def test_an_oversized_run_says_what_to_change(self) -> None:
        req = SimulationRequest(
            risks=tuple(
                RiskInput(risk_id=i, code=f"R{i}", p_occurrence=0.5, cost=pert(1, 2, 3))
                for i in range(1, 60)
            ),
            config=RunConfig(iterations=1_000_000, seed=1, memory_budget_mb=1.0),
        )
        with pytest.raises(RunTooLarge, match="Reduce iterations"):
            run(req)
