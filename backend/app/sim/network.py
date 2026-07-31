"""Critical path method, run across every iteration at once.

The engine needs a project finish date per iteration, and the only honest way to get one
is to run the network. Summing the delays mapped to activities is the schedule-side twin
of adding percentiles: it charges the project for delay on paths that had float to absorb
it, and it charges twice for two risks landing on the same path.

Everything here is vectorised over iterations rather than over activities. Both passes walk
the activities in topological order — that order is a genuine sequential dependency and no
amount of NumPy removes it — but each step operates on a whole column of iterations, so a
schedule of ``A`` activities and ``E`` relationships costs ``A + E`` array operations
regardless of how many iterations are running.

Two limits are deliberate and stated rather than approximated:

* **Constraints are not applied.** A mandatory finish date overrides network logic, and
  honouring one needs calendar arithmetic this package does not carry. What is offered
  instead is ``min_start_day`` on an activity: the caller converts a "start on or after"
  to working days from the data date, outside the sim, and the forward pass respects it.
  Anything harder is reported as a warning on the result, not silently ignored.
* **One calendar.** Durations are working days on the run's calendar, per the units
  invariant. Mixing calendars is the caller's problem to resolve before it gets here,
  because inside this module a day is just a float and there is nothing left to check it
  against.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np
from numpy.typing import NDArray

from app.sim.errors import NetworkCycle, SimulationInputInvalid

__all__ = ["CompiledNetwork", "FS", "SS", "FF", "SF", "REL_CODES"]

FS, SS, FF, SF = 0, 1, 2, 3
REL_CODES: dict[str, int] = {"FS": FS, "SS": SS, "FF": FF, "SF": SF}

#: Total float at or below this counts as critical. Durations are floats and a chain of a
#: few thousand additions accumulates rounding well below a millionth of a day, so the
#: tolerance separates "zero float" from "float that is genuinely small" rather than
#: papering over an error.
CRITICAL_TOLERANCE = 1e-6


class CompiledNetwork:
    """An activity network laid out for repeated vectorised passes.

    Built once per run. The topological order, the edge grouping and the index maps are
    all fixed by the network, so paying for them per chunk would be paying for them
    thousands of times.
    """

    __slots__ = (
        "activity_ids",
        "index",
        "n_activities",
        "topo",
        "min_start",
        "finish_idx",
        "_out_ptr",
        "_out_succ",
        "_out_type",
        "_out_lag",
        "_in_ptr",
        "_in_pred",
        "_in_type",
        "_in_lag",
    )

    def __init__(
        self,
        activity_ids: Sequence[str],
        edges: Sequence[tuple[str, str, str, float]],
        *,
        min_start: NDArray[np.float64] | None = None,
        finish_activity_ids: Sequence[str] = (),
    ) -> None:
        self.activity_ids = tuple(activity_ids)
        self.n_activities = len(self.activity_ids)
        if self.n_activities == 0:
            raise SimulationInputInvalid(["the schedule has no activities"])

        self.index = {a: i for i, a in enumerate(self.activity_ids)}
        if len(self.index) != self.n_activities:
            raise SimulationInputInvalid(["activity ids are not unique"])

        issues: list[str] = []
        pred_i: list[int] = []
        succ_i: list[int] = []
        type_i: list[int] = []
        lag_f: list[float] = []
        for p, s, t, lag in edges:
            if p not in self.index:
                issues.append(f"relationship predecessor {p!r} is not an activity")
                continue
            if s not in self.index:
                issues.append(f"relationship successor {s!r} is not an activity")
                continue
            if t not in REL_CODES:
                issues.append(f"relationship type {t!r} is not one of FS, SS, FF, SF")
                continue
            if p == s:
                issues.append(f"activity {p!r} is its own predecessor")
                continue
            pred_i.append(self.index[p])
            succ_i.append(self.index[s])
            type_i.append(REL_CODES[t])
            lag_f.append(float(lag))
        if issues:
            raise SimulationInputInvalid(issues)

        pred = np.array(pred_i, dtype=np.int64)
        succ = np.array(succ_i, dtype=np.int64)
        rtype = np.array(type_i, dtype=np.int8)
        lag = np.array(lag_f, dtype=np.float64)

        self._out_ptr, self._out_succ, self._out_type, self._out_lag = _group_by(
            pred, succ, rtype, lag, self.n_activities
        )
        self._in_ptr, self._in_pred, self._in_type, self._in_lag = _group_by(
            succ, pred, rtype, lag, self.n_activities
        )

        self.topo = _topological_order(
            self._out_ptr, self._out_succ, self.n_activities, self.activity_ids
        )

        if min_start is None:
            self.min_start = np.zeros(self.n_activities, dtype=np.float64)
        else:
            ms = np.asarray(min_start, dtype=np.float64)
            if ms.shape != (self.n_activities,):
                raise SimulationInputInvalid(
                    [f"min_start has shape {ms.shape}, expected ({self.n_activities},)"]
                )
            self.min_start = np.maximum(ms, 0.0)

        if finish_activity_ids:
            missing = [a for a in finish_activity_ids if a not in self.index]
            if missing:
                raise SimulationInputInvalid(
                    [f"finish activity {a!r} is not in the network" for a in missing]
                )
            self.finish_idx = np.array(
                [self.index[a] for a in finish_activity_ids], dtype=np.int64
            )
        else:
            self.finish_idx = np.arange(self.n_activities, dtype=np.int64)

    # -- passes -----------------------------------------------------------------

    def forward(
        self, dur: NDArray[np.float64]
    ) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        """Early start and early finish for every activity, every iteration.

        ``dur`` is ``(iterations, activities)`` in working days. Day zero is the data
        date.
        """
        self._check_dur(dur)
        n = dur.shape[0]
        # Column-major throughout both passes: every operation here reads and writes one
        # activity's column across all iterations, and in C order those elements sit a
        # whole row apart. On a five-thousand-activity network the layout alone is worth
        # about a third of the runtime.
        es = np.empty((n, self.n_activities), dtype=np.float64, order="F")
        es[:] = self.min_start[None, :]
        ef = np.empty((n, self.n_activities), dtype=np.float64, order="F")

        out_ptr, succ, rtype, lag = (
            self._out_ptr,
            self._out_succ,
            self._out_type,
            self._out_lag,
        )
        for a in self.topo:
            es_a = es[:, a]
            ef_a = es_a + dur[:, a]
            ef[:, a] = ef_a
            for e in range(out_ptr[a], out_ptr[a + 1]):
                s = succ[e]
                t = rtype[e]
                if t == FS:
                    bound = ef_a + lag[e]
                elif t == SS:
                    bound = es_a + lag[e]
                elif t == FF:
                    bound = ef_a + lag[e] - dur[:, s]
                else:  # SF
                    bound = es_a + lag[e] - dur[:, s]
                np.maximum(es[:, s], bound, out=es[:, s])
        return es, ef

    def backward(
        self, dur: NDArray[np.float64], project_finish: NDArray[np.float64]
    ) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        """Late start and late finish, given a forward pass and a finish per iteration.

        Every activity is seeded at the project finish and pulled back by its successors.
        An activity with no successors keeps the seed, which is the standard open-end
        rule; one with successors always ends up at or below it, since a late start can
        never exceed the late finish it came from.
        """
        self._check_dur(dur)
        lf = np.empty((dur.shape[0], self.n_activities), dtype=np.float64, order="F")
        lf[:] = project_finish[:, None]
        ls = np.empty_like(lf, order="F")

        in_ptr, pred, rtype, lag = (
            self._in_ptr,
            self._in_pred,
            self._in_type,
            self._in_lag,
        )
        for a in self.topo[::-1]:
            lf_a = lf[:, a]
            ls_a = lf_a - dur[:, a]
            ls[:, a] = ls_a
            for e in range(in_ptr[a], in_ptr[a + 1]):
                p = pred[e]
                t = rtype[e]
                if t == FS:
                    bound = ls_a - lag[e]
                elif t == SS:
                    bound = ls_a - lag[e] + dur[:, p]
                elif t == FF:
                    bound = lf_a - lag[e]
                else:  # SF
                    bound = lf_a - lag[e] + dur[:, p]
                np.minimum(lf[:, p], bound, out=lf[:, p])
        return ls, lf

    def project_finish(self, ef: NDArray[np.float64]) -> NDArray[np.float64]:
        """Finish day per iteration: the latest early finish among the finish set."""
        return ef[:, self.finish_idx].max(axis=1)

    def deterministic_finish(self, dur: NDArray[np.float64]) -> float:
        """Finish day for a single set of durations. Same code path as a run."""
        _, ef = self.forward(np.asarray(dur, dtype=np.float64).reshape(1, -1))
        return float(self.project_finish(ef)[0])

    # -- helpers ----------------------------------------------------------------

    def _check_dur(self, dur: NDArray[np.float64]) -> None:
        if dur.ndim != 2 or dur.shape[1] != self.n_activities:
            raise SimulationInputInvalid(
                [
                    f"durations have shape {dur.shape}, expected "
                    f"(iterations, {self.n_activities})"
                ]
            )


def _group_by(
    key: NDArray[np.int64],
    other: NDArray[np.int64],
    rtype: NDArray[np.int8],
    lag: NDArray[np.float64],
    n: int,
) -> tuple[NDArray[np.int64], NDArray[np.int64], NDArray[np.int8], NDArray[np.float64]]:
    """Compressed-row layout of the edge list, keyed by ``key``.

    A stable sort keeps edges in input order within a group, so two runs over the same
    parse walk the network identically — floating point addition is not associative and a
    reordering would move the last bits of the finish date.
    """
    order = np.argsort(key, kind="stable")
    counts = np.bincount(key, minlength=n)
    ptr = np.zeros(n + 1, dtype=np.int64)
    np.cumsum(counts, out=ptr[1:])
    return ptr, other[order], rtype[order], lag[order]


def _topological_order(
    out_ptr: NDArray[np.int64],
    out_succ: NDArray[np.int64],
    n: int,
    activity_ids: tuple[str, ...],
) -> NDArray[np.int64]:
    """Kahn's algorithm, taking ready activities in index order.

    The tie-break is by index rather than by a set's iteration order so the same network
    always produces the same walk, which reproducibility needs down to the bit.
    """
    indeg = np.zeros(n, dtype=np.int64)
    np.add.at(indeg, out_succ, 1)

    ready = [int(i) for i in np.flatnonzero(indeg == 0)]
    ready.sort()
    order: list[int] = []
    head = 0
    while head < len(ready):
        a = ready[head]
        head += 1
        order.append(a)
        for e in range(out_ptr[a], out_ptr[a + 1]):
            s = int(out_succ[e])
            indeg[s] -= 1
            if indeg[s] == 0:
                ready.append(s)

    if len(order) != n:
        stuck = np.flatnonzero(indeg > 0)
        raise NetworkCycle([activity_ids[int(i)] for i in stuck])
    return np.array(order, dtype=np.int64)
