"""Simulation domain errors.

Imported from ``app.core.errors`` rather than defining a parallel root, so the API layer
keeps translating one family. ``core.errors`` is pure Python with no framework or driver
imports, which is what lets a package that must stay side-effect free depend on it.
"""

from __future__ import annotations

from app.core.errors import RiskPlatformError


class SimulationError(RiskPlatformError):
    """Base class for every failure raised by the simulation engine."""


class SimulationInputInvalid(SimulationError):
    """The run cannot be assembled from the inputs as given.

    Carries every failing rule rather than the first, for the same reason
    :class:`~app.core.errors.QuantEstimateInvalid` does: an analyst correcting a run
    configuration one error per attempt stops correcting it.
    """

    def __init__(self, issues: list[str]) -> None:
        self.issues = issues
        super().__init__(
            f"Simulation inputs are not runnable ({len(issues)} problem(s)): "
            + "; ".join(issues)
        )


class NetworkCycle(SimulationError):
    """The activity network contains a loop, so no forward pass exists.

    P6 refuses to save a circular relationship, so a cycle here means the parse invented
    one or a synthetic inserted activity was wired back into its own predecessor. Named
    rather than swallowed: a CPM that silently drops an edge to break the loop produces a
    finish date nobody can trace.
    """

    def __init__(self, members: list[str]) -> None:
        self.members = members
        shown = ", ".join(members[:10])
        more = f" (+{len(members) - 10} more)" if len(members) > 10 else ""
        super().__init__(f"Activity network contains a cycle involving: {shown}{more}")


class RunTooLarge(SimulationError):
    """The run would allocate more memory than the configured budget allows.

    Raised up front with the arithmetic rather than discovered as a MemoryError halfway
    through, because the fix is a configuration change the caller can make immediately.
    """

    def __init__(
        self, what: str, needed_mb: float, budget_mb: float, remedy: str
    ) -> None:
        self.what = what
        self.needed_mb = needed_mb
        self.budget_mb = budget_mb
        super().__init__(
            f"{what} needs {needed_mb:,.0f} MB against a budget of {budget_mb:,.0f} MB. "
            f"{remedy}"
        )


class CorrelationNotRepairable(SimulationError):
    """The target correlation matrix could not be repaired into a usable one.

    Only reachable when a caller supplies explicit pairwise values that contradict each
    other badly enough that the nearest positive-definite matrix is still degenerate.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(f"Correlation matrix is unusable: {detail}")
