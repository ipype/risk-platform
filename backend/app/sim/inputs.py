"""What a run is made of.

Pydantic at the boundary, per the repo convention, and frozen throughout: a run's inputs
are hashed into its manifest, so anything that could be mutated after the hash is taken is
a reproducibility hole (invariant 6).

Two things this package deliberately does **not** do, both because doing them would need
the database or the schedule domain and this package may have neither:

* **Bound recovery.** ``p10_p90`` bounds are widened into absolute support by
  ``quant_validation.absolute_bounds`` before a :class:`~app.sim.distributions.DistributionSpec`
  is built. By the time a spec arrives here the numbers mean what they say.
* **Scope resolution.** A ``scoped_driver`` mapping is a filter over a schedule version.
  The adapter resolves it against the current parse and passes the resulting activity ids
  in ``activity_ids``, which is also what keeps the resolution in one place rather than
  two.

The cost model is one sentence, and everything else follows from it::

    total_cost_i = base_cost + sum_r contribution_r_i + burn_rate * max(delay_i, 0)

``contribution_r_i`` is the risk's own sampled cost for that iteration: zero when the risk
did not occur, the elicited amount when it did, or that amount as a percentage of a base
reference when ``cost_basis`` says so. An estimate elicited as a total rather than an
increment — "the base is 8 to 12 million" — is expressed by leaving ``base_cost`` at zero
and carrying the whole range on a variability row. There is no third option, on purpose:
two ways to say the same thing is how two runs of one register end up disagreeing.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.sim.distributions import DistributionSpec
from app.sim.errors import SimulationInputInvalid

__all__ = [
    "ActivityInput",
    "CorrelationInput",
    "DriverSpec",
    "PairCorrelation",
    "RelationshipInput",
    "RiskInput",
    "RiskMappingInput",
    "RunConfig",
    "ScheduleInput",
    "SimulationRequest",
]

MappingType = Literal["duration_driver", "inserted_activity", "scoped_driver"]


class RiskMappingInput(BaseModel):
    """Where one risk lands on the network.

    ``duration_driver`` and ``scoped_driver`` share a semantic: one sampled delay per risk
    per iteration, added to *every* activity in ``activity_ids``. Not divided among them.
    That is the Hulett risk-driver method and it is what makes the driven activities come
    out correlated without anyone building a correlation matrix by hand — a weather risk
    driving five concurrent excavations rains on all five, and a productivity risk running
    through five sequential packages really does cost five times. Splitting the draw would
    destroy exactly the property the method exists for, which is why the API refuses
    ``allocation_pct`` on both.

    ``inserted_activity`` is the opposite case: the risk adds work that is not in the
    schedule, and sixty days spread over three insertion points is not sixty at each. Here
    allocation is meaningful, and an unset allocation splits evenly across the risk's
    insertion points rather than replicating.
    """

    model_config = ConfigDict(frozen=True)

    mapping_type: MappingType
    #: Driven activities. For ``scoped_driver`` these are the adapter's resolution of the
    #: scope filter against the schedule version being simulated.
    activity_ids: tuple[str, ...] = ()
    predecessor_id: str | None = None
    successor_id: str | None = None
    #: Percent, 0-100, matching the API field it comes from.
    allocation_pct: float | None = Field(default=None, ge=0.0, le=100.0)

    @model_validator(mode="after")
    def _check(self) -> RiskMappingInput:
        if self.mapping_type == "inserted_activity":
            if not self.predecessor_id or not self.successor_id:
                raise ValueError(
                    "inserted_activity needs both a predecessor and a successor"
                )
        else:
            if not self.activity_ids:
                raise ValueError(f"{self.mapping_type} needs at least one activity")
            if self.allocation_pct is not None:
                raise ValueError(
                    f"allocation_pct does not apply to {self.mapping_type}: the sampled "
                    "delay is shared across driven activities, not divided among them"
                )
        return self


class RiskInput(BaseModel):
    """One risk, quantified, ready to sample."""

    model_config = ConfigDict(frozen=True)

    risk_id: int
    code: str = ""
    title: str = ""
    p_occurrence: float = Field(default=1.0, gt=0.0, le=1.0)
    #: An inherent range on a base estimate rather than a discrete event. Occurrence is
    #: certain by definition, which is checked rather than assumed.
    is_variability: bool = False

    cost: DistributionSpec | None = None
    cost_basis: Literal["absolute", "pct_of_base"] = "absolute"
    #: The amount a ``pct_of_base`` figure is a percentage of. Falls back to
    #: ``RunConfig.base_cost`` when unset.
    cost_base_reference: float | None = None

    #: Working days on the run calendar. Always an absolute duration — the schema carries
    #: no percentage basis for schedule impact, and inventing one here would mean two
    #: places deciding what a schedule number means.
    sched: DistributionSpec | None = None

    #: Shared-cause tags. Two risks tagged with the same driver correlate at that driver's
    #: coefficient without anyone eliciting the pair.
    drivers: tuple[str, ...] = ()
    mappings: tuple[RiskMappingInput, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> RiskInput:
        if self.is_variability and self.p_occurrence != 1.0:
            raise ValueError(
                "a variability row is certain by definition; p_occurrence must be 1.0"
            )
        if self.cost_basis == "pct_of_base" and self.cost is None:
            raise ValueError("cost_basis is set but no cost distribution was supplied")
        if self.sched is not None and not self.mappings:
            raise ValueError(
                f"risk {self.risk_id} carries a schedule impact but no activity mapping, "
                "so the delay has nowhere to land"
            )
        if self.sched is None and self.mappings:
            raise ValueError(
                f"risk {self.risk_id} is mapped to the schedule but has no elicited "
                "schedule impact, so the mapping would contribute nothing"
            )
        return self

    @property
    def insertion_count(self) -> int:
        return sum(1 for m in self.mappings if m.mapping_type == "inserted_activity")


class ActivityInput(BaseModel):
    """One activity of the network being simulated.

    ``duration_days`` is *remaining* work. A completed activity is zero, an in-progress one
    carries what is left; converting from the parse is the adapter's job because it needs
    the data date and the calendars.

    ``uncertainty`` replaces the duration for sampled iterations — it is an absolute
    duration in days, not a multiplier. Leaving it unset makes the activity deterministic,
    which is what completed work should be. Modelling only discrete risks and leaving every
    duration fixed produces an unrealistically tight base distribution, so an empty
    uncertainty across the whole network is reported as a warning on the result.
    """

    model_config = ConfigDict(frozen=True)

    activity_id: str
    code: str = ""
    name: str = ""
    duration_days: float = Field(ge=0.0)
    uncertainty: DistributionSpec | None = None
    #: Working days from the data date. The adapter's conversion of a "start on or after"
    #: constraint; the forward pass applies nothing else.
    min_start_day: float | None = Field(default=None, ge=0.0)
    is_milestone: bool = False
    has_hard_constraint: bool = False


class RelationshipInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    predecessor_id: str
    successor_id: str
    type: Literal["FS", "SS", "FF", "SF"] = "FS"
    lag_days: float = 0.0


class ScheduleInput(BaseModel):
    """The network, already normalised onto one calendar."""

    model_config = ConfigDict(frozen=True)

    #: The calendar every duration here was measured against. Carried rather than assumed,
    #: per the units invariant — inside the engine a day is just a float, so this is the
    #: last point at which the question can be asked at all.
    calendar_id: str
    activities: tuple[ActivityInput, ...]
    relationships: tuple[RelationshipInput, ...] = ()
    #: Activities whose finish defines the project finish. Empty means the latest finish
    #: anywhere, which is right for a schedule with one open end and wrong for one with a
    #: detached tail — hence the option.
    finish_activity_ids: tuple[str, ...] = ()


class DriverSpec(BaseModel):
    """A shared cause and the correlation it implies between the risks that carry it."""

    model_config = ConfigDict(frozen=True)

    name: str
    coefficient: float = Field(default=0.5, ge=-1.0, le=1.0)


class PairCorrelation(BaseModel):
    """An explicit pairwise value, overriding whatever the drivers imply."""

    model_config = ConfigDict(frozen=True)

    risk_a: int
    risk_b: int
    coefficient: float = Field(ge=-1.0, le=1.0)


class CorrelationInput(BaseModel):
    """How the correlation matrix is assembled.

    Driver tagging is O(n) for the analyst and pairwise elicitation is O(n^2), which
    collapses somewhere around fifteen risks. Where two risks share more than one driver
    the strongest is taken rather than any kind of sum: correlations do not add, and a sum
    would run past 1.0 on the third shared tag.
    """

    model_config = ConfigDict(frozen=True)

    drivers: tuple[DriverSpec, ...] = ()
    pairs: tuple[PairCorrelation, ...] = ()


class RunConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    iterations: int = Field(default=10_000, ge=100, le=1_000_000)
    #: Stored in the manifest and replayable. Not optional, and not defaulted to
    #: something time-derived — a run nobody can repeat is not evidence.
    seed: int = 12345
    sampling: Literal["lhs", "mc"] = "lhs"
    #: Place each Latin hypercube draw at its stratum midpoint. Removes sampling noise
    #: entirely, which is what a statistical regression test wants and what production
    #: must not have: the strata stop being a sample and the spread stops being estimable.
    centered_lhs: bool = False

    #: Iterations processed per CPM pass. ``None`` picks a value from the network size and
    #: records it in the manifest, so the choice stays part of the reproducibility record
    #: rather than a property of the machine that happened to run it.
    chunk_size: int | None = Field(default=None, ge=16, le=100_000)

    base_cost: float = Field(default=0.0, ge=0.0)
    #: Cost per day of schedule delay: extended overheads, supervision, plant standing
    #: time, escalation. Multiplied by the delay *inside* each iteration (invariant 1).
    burn_rate_per_day: float = Field(default=0.0, ge=0.0)
    #: Whether an iteration finishing early may reduce total cost. Off by default: a burn
    #: rate is an extended-overhead figure, and the saving from finishing early is a
    #: different and usually much smaller number nobody elicited.
    allow_negative_delay_credit: bool = False

    correlate_occurrence: bool = True
    #: Correlation between a single risk's own cost and schedule draws. Zero by default
    #: because nobody elicits it, and visible rather than buried because zero is a real
    #: modelling choice: a risk whose delay and cost are physically the same event has
    #: them linked through the burn-rate term and nowhere else.
    intra_risk_cost_sched_correlation: float = Field(default=0.0, ge=-1.0, le=1.0)

    percentiles: tuple[float, ...] = (5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 95)
    s_curve_points: int = Field(default=101, ge=11, le=1001)
    histogram_bins: int = Field(default=50, ge=5, le=500)

    memory_budget_mb: float = Field(default=1024.0, gt=0.0)
    #: Cap on how many activities get a duration-sensitivity figure. Criticality index is
    #: reported for every activity because it is free; sensitivity needs accumulated
    #: statistics per column and there is no reading to be had from five thousand rows.
    max_sensitivity_activities: int = Field(default=200, ge=10, le=5000)

    @model_validator(mode="after")
    def _check(self) -> RunConfig:
        if not self.percentiles:
            raise ValueError("at least one percentile is required")
        if any(not 0.0 < p < 100.0 for p in self.percentiles):
            raise ValueError("percentiles must lie strictly between 0 and 100")
        if len(set(self.percentiles)) != len(self.percentiles):
            raise ValueError("percentiles must be unique")
        return self


class SimulationRequest(BaseModel):
    """Everything a run needs, and the thing whose hash identifies it."""

    model_config = ConfigDict(frozen=True)

    risks: tuple[RiskInput, ...] = ()
    schedule: ScheduleInput | None = None
    correlation: CorrelationInput = CorrelationInput()
    config: RunConfig = RunConfig()

    @model_validator(mode="after")
    def _check(self) -> SimulationRequest:
        issues: list[str] = []

        ids = [r.risk_id for r in self.risks]
        if len(set(ids)) != len(ids):
            issues.append("risk ids are not unique")

        if self.schedule is None:
            for r in self.risks:
                if r.mappings:
                    issues.append(
                        f"risk {r.risk_id} is mapped to activities but no schedule was "
                        "supplied"
                    )
                    break
            if self.config.burn_rate_per_day > 0:
                issues.append(
                    "a burn rate was given with no schedule, so there is no delay for it "
                    "to price"
                )
        else:
            known = {a.activity_id for a in self.schedule.activities}
            if len(known) != len(self.schedule.activities):
                issues.append("activity ids are not unique")
            for r in self.risks:
                for m in r.mappings:
                    for a in m.activity_ids:
                        if a not in known:
                            issues.append(
                                f"risk {r.risk_id} is mapped to unknown activity {a!r}"
                            )
                    for label, aid in (
                        ("predecessor", m.predecessor_id),
                        ("successor", m.successor_id),
                    ):
                        if aid is not None and aid not in known:
                            issues.append(
                                f"risk {r.risk_id} names unknown {label} {aid!r}"
                            )

        driver_names = {d.name for d in self.correlation.drivers}
        for r in self.risks:
            unknown = [d for d in r.drivers if d not in driver_names]
            if unknown:
                issues.append(
                    f"risk {r.risk_id} carries undeclared driver(s) "
                    f"{', '.join(sorted(unknown))}"
                )

        by_id = set(ids)
        for p in self.correlation.pairs:
            if p.risk_a == p.risk_b:
                issues.append(f"risk {p.risk_a} is correlated with itself")
            for rid in (p.risk_a, p.risk_b):
                if rid not in by_id:
                    issues.append(f"correlation pair names unknown risk {rid}")

        if issues:
            raise SimulationInputInvalid(sorted(set(issues)))
        return self

    def fingerprint(self) -> str:
        """SHA-256 over the canonical serialisation of the whole request.

        Invariant 6's anchor. Field order is fixed by the model definitions and every
        model is frozen, so the same request always produces the same digest and a stored
        run can prove which inputs it came from.
        """
        payload = self.model_dump_json()
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
