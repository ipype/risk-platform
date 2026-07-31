"""CPM. Every case here is small enough to work by hand, which is the point."""

from __future__ import annotations

import numpy as np
import pytest

from app.sim.errors import NetworkCycle, SimulationInputInvalid
from app.sim.network import CRITICAL_TOLERANCE, CompiledNetwork


def diamond() -> CompiledNetwork:
    #   A -> B -> D
    #   A -> C -> D
    return CompiledNetwork(
        ["A", "B", "C", "D"],
        [
            ("A", "B", "FS", 0.0),
            ("B", "D", "FS", 0.0),
            ("A", "C", "FS", 0.0),
            ("C", "D", "FS", 0.0),
        ],
    )


class TestForwardPass:
    def test_longest_path_wins(self) -> None:
        net = diamond()
        dur = np.array([[5.0, 3.0, 10.0, 2.0]])
        es, ef = net.forward(dur)
        assert list(es[0]) == [0.0, 5.0, 5.0, 15.0]
        assert net.project_finish(ef)[0] == pytest.approx(17.0)

    def test_lag_pushes_the_successor(self) -> None:
        net = CompiledNetwork(["A", "B"], [("A", "B", "FS", 4.0)])
        _, ef = net.forward(np.array([[10.0, 5.0]]))
        assert net.project_finish(ef)[0] == pytest.approx(19.0)

    def test_negative_lag_overlaps_without_going_before_time_zero(self) -> None:
        net = CompiledNetwork(["A", "B"], [("A", "B", "FS", -3.0)])
        es, _ = net.forward(np.array([[10.0, 5.0]]))
        assert es[0][1] == pytest.approx(7.0)
        assert es.min() >= 0.0

    def test_all_four_relationship_types(self) -> None:
        net = CompiledNetwork(
            ["S", "X", "Y", "F"],
            [
                ("S", "X", "FS", 2.0),
                ("X", "Y", "SS", 3.0),
                ("Y", "F", "FF", 1.0),
                ("X", "F", "FS", 0.0),
            ],
        )
        es, ef = net.forward(np.array([[0.0, 4.0, 6.0, 0.0]]))
        # X starts at 2 (FS+2 from a zero-duration start), finishes 6.
        # Y starts at 5 (SS+3 off X), finishes 11.
        # F must finish at least 1 after Y, so 12.
        assert list(es[0]) == [0.0, 2.0, 5.0, 12.0]
        assert net.project_finish(ef)[0] == pytest.approx(12.0)

    def test_min_start_holds_an_activity_back(self) -> None:
        net = CompiledNetwork(
            ["A", "B"],
            [("A", "B", "FS", 0.0)],
            min_start=np.array([0.0, 40.0]),
        )
        es, ef = net.forward(np.array([[10.0, 5.0]]))
        assert es[0][1] == pytest.approx(40.0)
        assert net.project_finish(ef)[0] == pytest.approx(45.0)

    def test_runs_every_iteration_independently(self) -> None:
        net = diamond()
        dur = np.array([[5.0, 3.0, 10.0, 2.0], [5.0, 30.0, 10.0, 2.0]])
        _, ef = net.forward(dur)
        assert list(net.project_finish(ef)) == [17.0, 37.0]


class TestBackwardPass:
    def test_float_lands_on_the_off_path_activity(self) -> None:
        net = diamond()
        dur = np.array([[5.0, 3.0, 10.0, 2.0]])
        es, ef = net.forward(dur)
        pf = net.project_finish(ef)
        ls, _ = net.backward(dur, pf)
        assert list((ls - es)[0]) == [0.0, 7.0, 0.0, 0.0]

    def test_criticality_follows_the_sampled_durations(self) -> None:
        net = diamond()
        # B is long in the second iteration, so the critical path swaps.
        dur = np.array([[5.0, 3.0, 10.0, 2.0], [5.0, 20.0, 10.0, 2.0]])
        es, ef = net.forward(dur)
        pf = net.project_finish(ef)
        ls, _ = net.backward(dur, pf)
        critical = (ls - es) <= CRITICAL_TOLERANCE
        assert list(critical[0]) == [True, False, True, True]
        assert list(critical[1]) == [True, True, False, True]

    def test_open_ends_are_seeded_at_the_project_finish(self) -> None:
        net = CompiledNetwork(["A", "B"], [])
        dur = np.array([[10.0, 3.0]])
        es, ef = net.forward(dur)
        pf = net.project_finish(ef)
        ls, lf = net.backward(dur, pf)
        assert list(lf[0]) == [10.0, 10.0]
        assert list((ls - es)[0]) == [0.0, 7.0]


class TestFinishSet:
    def test_named_finish_activity_ignores_a_detached_tail(self) -> None:
        net = CompiledNetwork(
            ["A", "B", "JUNK"],
            [("A", "B", "FS", 0.0)],
            finish_activity_ids=["B"],
        )
        _, ef = net.forward(np.array([[5.0, 5.0, 900.0]]))
        assert net.project_finish(ef)[0] == pytest.approx(10.0)

    def test_default_finish_is_the_latest_anywhere(self) -> None:
        net = CompiledNetwork(["A", "B", "JUNK"], [("A", "B", "FS", 0.0)])
        _, ef = net.forward(np.array([[5.0, 5.0, 900.0]]))
        assert net.project_finish(ef)[0] == pytest.approx(900.0)

    def test_deterministic_finish_uses_the_same_code_path(self) -> None:
        net = diamond()
        assert net.deterministic_finish(np.array([5.0, 3.0, 10.0, 2.0])) == 17.0


class TestRejections:
    def test_names_the_activities_in_a_cycle(self) -> None:
        with pytest.raises(NetworkCycle) as exc:
            CompiledNetwork(
                ["A", "B", "C"],
                [("A", "B", "FS", 0.0), ("B", "C", "FS", 0.0), ("C", "A", "FS", 0.0)],
            )
        assert set(exc.value.members) == {"A", "B", "C"}

    def test_rejects_an_unknown_endpoint(self) -> None:
        with pytest.raises(SimulationInputInvalid, match="GHOST"):
            CompiledNetwork(["A"], [("A", "GHOST", "FS", 0.0)])

    def test_rejects_a_self_loop(self) -> None:
        with pytest.raises(SimulationInputInvalid, match="own predecessor"):
            CompiledNetwork(["A"], [("A", "A", "FS", 0.0)])

    def test_rejects_a_bad_relationship_type(self) -> None:
        with pytest.raises(SimulationInputInvalid, match="FS, SS, FF, SF"):
            CompiledNetwork(["A", "B"], [("A", "B", "XX", 0.0)])

    def test_rejects_duplicate_activity_ids(self) -> None:
        with pytest.raises(SimulationInputInvalid, match="not unique"):
            CompiledNetwork(["A", "A"], [])

    def test_reports_every_bad_edge_at_once(self) -> None:
        with pytest.raises(SimulationInputInvalid) as exc:
            CompiledNetwork(["A"], [("A", "X", "FS", 0.0), ("Y", "A", "FS", 0.0)])
        assert len(exc.value.issues) == 2

    def test_rejects_a_duration_matrix_of_the_wrong_width(self) -> None:
        with pytest.raises(SimulationInputInvalid, match="expected"):
            diamond().forward(np.zeros((2, 3)))


class TestOrderStability:
    def test_the_walk_does_not_depend_on_edge_input_order(self) -> None:
        edges = [
            ("A", "B", "FS", 0.0),
            ("B", "D", "FS", 0.0),
            ("A", "C", "FS", 0.0),
            ("C", "D", "FS", 0.0),
        ]
        dur = np.array([[5.0, 3.0, 10.0, 2.0]])
        a = CompiledNetwork(["A", "B", "C", "D"], edges)
        b = CompiledNetwork(["A", "B", "C", "D"], list(reversed(edges)))
        assert np.array_equal(a.forward(dur)[1], b.forward(dur)[1])
