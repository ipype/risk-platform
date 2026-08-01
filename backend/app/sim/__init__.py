"""Monte Carlo cost and schedule risk simulation.

Pure by construction: no database, no network, no logging, no clock. A
:class:`~app.sim.inputs.SimulationRequest` goes in and an
:class:`~app.sim.engine.Outcome` comes out, and the same request always produces the same
numbers. That is not tidiness for its own sake — invariant 6 says a run must be
reproducible, and the only cheap way to guarantee it is to have nothing in here that could
vary.

Typical use::

    from app.sim import SimulationRequest, run

    outcome = run(request)
    p80 = next(p.value for p in outcome.result.contingency.contingency if p.p == 80)

Assembling a request from the register, the quantitative estimates and a parsed schedule
belongs to an adapter in ``services``, which is where the database and the scope-filter
resolution live. This package deliberately cannot reach either.
"""

from app.sim.correlation import CorrelationReport
from app.sim.distributions import DistributionSpec, PointMass, spec_from_moments
from app.sim.engine import ENGINE_VERSION, Outcome, RunArrays, SimulationResult, run
from app.sim.errors import (
    CorrelationNotRepairable,
    NetworkCycle,
    RunTooLarge,
    SimulationError,
    SimulationInputInvalid,
)
from app.sim.inputs import (
    ActivityInput,
    CorrelationInput,
    DriverSpec,
    PairCorrelation,
    RelationshipInput,
    RiskInput,
    RiskMappingInput,
    RunConfig,
    ScheduleInput,
    SimulationRequest,
)
from app.sim.joint import JointConfidence, JointFrontier, JointPoint, joint_confidence
from app.sim.network import CompiledNetwork
from app.sim.results import (
    ContingencyView,
    DeterministicView,
    PercentilePoint,
    RunManifest,
    SeriesSummary,
)
from app.sim.sensitivity import ActivityCriticality, RiskSensitivity

__all__ = [
    "ENGINE_VERSION",
    "ActivityCriticality",
    "ActivityInput",
    "CompiledNetwork",
    "ContingencyView",
    "CorrelationInput",
    "CorrelationNotRepairable",
    "CorrelationReport",
    "DeterministicView",
    "DistributionSpec",
    "DriverSpec",
    "JointConfidence",
    "JointFrontier",
    "JointPoint",
    "NetworkCycle",
    "Outcome",
    "PairCorrelation",
    "PercentilePoint",
    "PointMass",
    "RelationshipInput",
    "RiskInput",
    "RiskMappingInput",
    "RiskSensitivity",
    "RunArrays",
    "RunConfig",
    "RunManifest",
    "RunTooLarge",
    "ScheduleInput",
    "SeriesSummary",
    "SimulationError",
    "SimulationInputInvalid",
    "SimulationRequest",
    "SimulationResult",
    "joint_confidence",
    "run",
    "spec_from_moments",
]
