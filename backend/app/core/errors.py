"""Domain error hierarchy.

Every error raised by domain code subclasses :class:`RiskPlatformError` so that API
layers can translate one family of exceptions instead of guessing at builtins.
"""

from __future__ import annotations


class RiskPlatformError(Exception):
    """Base class for every domain error raised by this platform."""


class ScheduleError(RiskPlatformError):
    """Base class for schedule ingestion and analysis failures."""


class UnsupportedScheduleFormat(ScheduleError):
    """No registered parser claims the given file."""

    def __init__(self, suffix: str, supported: list[str]) -> None:
        self.suffix = suffix
        self.supported = supported
        super().__init__(
            f"No parser is registered for '{suffix}'. Supported: {', '.join(supported)}."
        )


class ParserUnavailable(ScheduleError):
    """A parser exists for this format but its runtime dependency is missing.

    Distinct from :class:`UnsupportedScheduleFormat`: the format is understood, the
    environment simply cannot handle it yet (for example ``.mpp`` without a JRE).
    """

    def __init__(self, suffix: str, reason: str) -> None:
        self.suffix = suffix
        self.reason = reason
        super().__init__(f"Cannot parse '{suffix}' in this deployment: {reason}")


class MalformedScheduleFile(ScheduleError):
    """The file is the right format but its content could not be read."""


class AmbiguousProjectError(ScheduleError):
    """The file holds several projects and the caller did not say which one to use.

    Never guessed at. Picking the wrong project silently produces a complete,
    plausible, and entirely wrong risk analysis.
    """

    def __init__(self, candidates: list[tuple[str, str, int]]) -> None:
        self.candidates = candidates
        listing = ", ".join(
            f"{pid} ({name}, {count} activities)" for pid, name, count in candidates
        )
        super().__init__(
            f"File contains {len(candidates)} projects; specify project_id. Found: {listing}"
        )


class ScheduleDeleteBlocked(ScheduleError):
    """Deleting this version would destroy accepted analyst work.

    Raised rather than silently cascading. A ``proposed`` mapping is a suggestion nobody
    has ruled on and costs nothing to lose; an ``accepted`` one is a decision an analyst
    made about where a risk lands on the network, and it is not recoverable from the
    source file. The caller re-sends with ``force=true`` once it has said so on screen.
    """

    def __init__(self, version_id: int, accepted: int, proposed: int) -> None:
        self.version_id = version_id
        self.accepted = accepted
        self.proposed = proposed
        super().__init__(
            f"Schedule version {version_id} carries {accepted} accepted risk-to-activity "
            f"mapping(s) ({proposed} proposed). Deleting it removes them. Re-send with "
            "force=true to confirm."
        )


class ProjectNotFound(ScheduleError):
    """The requested project id is not present in the file."""

    def __init__(self, project_id: str, available: list[str]) -> None:
        self.project_id = project_id
        self.available = available
        super().__init__(
            f"Project '{project_id}' not in file. Available: {', '.join(available) or 'none'}"
        )


class SimulationNotAssemblable(RiskPlatformError):
    """A run cannot be built from the register, the estimates and the schedule as they are.

    Distinct from :class:`~app.sim.errors.SimulationInputInvalid`, which the engine raises
    about a request it has already been handed. This one is raised before a request
    exists, and every issue in it names something an analyst can go and fix.
    """

    def __init__(self, issues: list[str]) -> None:
        self.issues = issues
        super().__init__(
            f"Simulation cannot be assembled ({len(issues)} problem(s)): "
            + "; ".join(issues)
        )


class SimulationRunNotCancellable(RiskPlatformError):
    """The run is not in a state a cancel can act on.

    Only a run still sitting in ``queued`` can be cancelled: nothing has started, and
    revoking the task is enough to guarantee nothing ever will. A run already computing
    has partial CPU sunk into it and a worker to signal, not just a queue entry to drop; a
    run already terminal has nothing left to stop. Both are a different feature from this
    one, not a wider version of it.
    """

    def __init__(self, run_id: int, status: str) -> None:
        self.run_id = run_id
        self.status = status
        super().__init__(
            f"Run {run_id} is '{status}', not 'queued', so it cannot be cancelled."
        )


class ScheduleGateBlocked(RiskPlatformError):
    """The schedule has not passed the DCMA 14-point gate (invariant 3).

    Not a validation failure and not a permissions failure: the request is well formed and
    the caller is allowed to make it. What is missing is a decision — fix the schedule, or
    take responsibility for simulating one that failed. The override is a field on the run
    record, so the decision travels with the number it produced.
    """

    def __init__(self, version_id: int, message: str, blocking: list) -> None:
        self.version_id = version_id
        self.blocking = blocking
        super().__init__(message)


class ScopeError(RiskPlatformError):
    """Base class for hierarchy failures."""


class ScopeNotFound(ScopeError):
    """A scope id was named that does not exist."""

    def __init__(self, scope_id: int) -> None:
        self.scope_id = scope_id
        super().__init__(f"Scope {scope_id} does not exist.")


class ScopeInvalid(ScopeError):
    """The hierarchy would not survive the change being asked for.

    Containment order, cycles, authoring at the wrong level, deleting a node that still
    owns work. Every message names what to do instead, because every one of these is a
    decision the analyst can make rather than a fault they have to report.
    """


class ScopeDeleteBlocked(ScopeError):
    """The node still has children or still owns rows."""

    def __init__(self, scope_id: int, name: str, reasons: list[str]) -> None:
        self.scope_id = scope_id
        self.reasons = reasons
        super().__init__(
            f"{name!r} cannot be deleted yet: " + "; ".join(reasons)
        )


class QuantError(RiskPlatformError):
    """Base class for quantitative elicitation failures."""


class QuantEstimateInvalid(QuantError):
    """The estimate cannot be sampled as given.

    Carries every failing rule rather than the first one, because an SME correcting a
    form one error per round trip stops correcting it.
    """

    def __init__(self, issues: list[dict]) -> None:
        self.issues = issues
        summary = "; ".join(f"{i['field']}: {i['message']}" for i in issues)
        super().__init__(f"Estimate is not simulable. {summary}")



class QuantEstimateLocked(QuantError):
    """The estimate is frozen against a simulation run.

    Blocked rather than versioned-around: a run whose inputs moved after the fact is not
    reproducible, and reproducibility is the whole basis for defending the number.
    """

    def __init__(self, risk_id: int, scenario: str) -> None:
        self.risk_id = risk_id
        self.scenario = scenario
        super().__init__(
            f"The {scenario.replace('_', ' ')} estimate for risk {risk_id} is locked "
            "against a simulation run. Unlock it explicitly before editing."
        )
