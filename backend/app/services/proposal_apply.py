"""Turning a disposed proposal into a domain write.

**This is the only path from the ledger into a domain table.** A generator cannot reach a
risk, an estimate or a mapping except through a proposal a human has accepted, and the
appliers below are where that crossing happens. Keeping it in one module rather than
letting each route apply its own suggestions is what makes invariant 4 checkable: there is
one place to read to know that nothing generated writes directly, and it is this one.

**An applier writes through the same code a human edit uses.** The risk applier below
mutates the ORM object and records a ``RiskHistory`` row in the shape ``PATCH /risks/{id}``
produces, with ``provenance`` set. The alternative — a parallel write path for accepted
suggestions — would drift from the human one field by field, and the first thing to drift
would be the scoring, which is the thing a reviewer is least likely to notice.

**Registration is by ``target_type``, and an unknown one raises.** Silently ignoring a type
the registry does not know would let a proposal be marked accepted with nothing applied,
which is the one outcome the ledger must never be able to produce.

**Creation is a second registry, not a wider applier.** A proposal with ``target_id IS
NULL`` makes a row rather than changing one, and the two have almost nothing in common: a
creation has no prior value to diff against, no staleness question, no field whitelist, and
one whole-row payload instead of a single ``field_path``. Folding them into one function
would mean every applier opening with a branch on whether its target exists. They are
registered separately and dispatched separately, and an unknown creator raises exactly as
an unknown applier does.

Only the ``risk`` applier and the ``risk`` creator ship so far. Estimates and mappings
arrive with the generators that propose them, so their appliers can be written against a
real payload rather than an imagined one.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ProposalTargetInvalid
from app.models.history import RiskHistory, creation_changes, snapshot
from app.models.matrix import band_for, get_active_config, overall_impact
from app.models.proposal import Proposal
from app.models.rbs import RbsCategory, RbsSubcategory
from app.models.risk import Risk
from app.models.scope import OWNING_KIND, ScopeNode
from app.services.risk_code import next_code

__all__ = [
    "apply",
    "observe",
    "register",
    "register_creator",
    "APPLIABLE_RISK_FIELDS",
    "CREATABLE_RISK_FIELDS",
]

Applier = Callable[..., Awaitable[None]]
Observer = Callable[..., Awaitable[Any]]
Creator = Callable[..., Awaitable[int]]

#: Which risk fields a proposal may set. Narrower than ``RiskUpdate`` on purpose:
#: ``status`` is a workflow decision, ``risk_level`` and ``impact`` are *derived* by the
#: scoring pass below, and ``custom_fields`` is a free-form dict whose keys the ledger
#: cannot address with a single ``field_path``. A generator that wants any of these is
#: asking for a feature, not a wider whitelist.
APPLIABLE_RISK_FIELDS: frozenset[str] = frozenset(
    {
        "title",
        "description",
        "causes",
        "consequences",
        "probability",
        "impact_scores",
        "target_probability",
        "target_impact_scores",
        "mitigation_actions",
        "owner",
        "comments",
    }
)

#: What a *creation* payload may carry. Narrower than the register's own ``RiskCreate``
#: and deliberately so: no ``probability``, no ``impact_scores``, no ``status``, no
#: ``owner``. Identification says what the risk is; the numbers come from an elicitation
#: with the people who own the work, and a generator that ships an unreviewed probability
#: inside a creation payload gets it accepted as a side effect of accepting the risk
#: statement — one click, two decisions, one of them invisible.
CREATABLE_RISK_FIELDS: frozenset[str] = frozenset(
    {"subcategory_prefix", "title", "description", "causes", "consequences"}
)

#: Of those, the ones without which the row would be meaningless.
REQUIRED_RISK_FIELDS: tuple[str, ...] = ("subcategory_prefix", "title")

_APPLIERS: dict[str, Applier] = {}
_OBSERVERS: dict[str, Observer] = {}
_CREATORS: dict[str, Creator] = {}


def register(target_type: str, applier: Applier, observer: Observer) -> None:
    """Wire a target type into the ledger. Called at import time by this module."""
    _APPLIERS[target_type] = applier
    _OBSERVERS[target_type] = observer


def register_creator(target_type: str, creator: Creator) -> None:
    """Wire a target type's *creation* path. Returns the new row's id."""
    _CREATORS[target_type] = creator


async def apply(
    db: AsyncSession,
    proposal: Proposal,
    *,
    value: Any,
    actor: str,
    proposal_id: int,
) -> None:
    """Write an accepted or edited value onto its target.

    Raises rather than returning a failure, because the caller's contract is that a
    disposition and its application succeed or fail together.
    """
    if proposal.target_id is None:
        creator = _CREATORS.get(proposal.target_type)
        if creator is None:
            raise ProposalTargetInvalid(
                f"Nothing knows how to create a {proposal.target_type!r} from a "
                "proposal. Creatable targets: "
                f"{', '.join(sorted(_CREATORS)) or 'none'}."
            )
        created_id = await creator(
            db, proposal, value=value, actor=actor, proposal_id=proposal_id
        )
        # Written here rather than inside each creator so no creator can forget it. A
        # creation proposal whose accepted row cannot be found again is a decision with
        # nothing traceable behind it, which is the outcome the whole ledger exists to
        # make impossible.
        proposal.created_target_id = created_id
        return

    applier = _APPLIERS.get(proposal.target_type)
    if applier is None:
        raise ProposalTargetInvalid(
            f"Nothing knows how to apply a {proposal.target_type!r} proposal. "
            f"Known targets: {', '.join(sorted(_APPLIERS)) or 'none'}."
        )
    await applier(db, proposal, value=value, actor=actor, proposal_id=proposal_id)


async def observe(db: AsyncSession, proposal: Proposal) -> Any:
    """The target field's value right now, for the staleness check.

    Returns ``None`` when the type has no observer, which reads as "cannot tell" and lets
    the accept through. Blocking on an unknown observer would make the staleness guard a
    second, accidental whitelist.
    """
    observer = _OBSERVERS.get(proposal.target_type)
    if observer is None:
        return None
    return await observer(db, proposal)


# --------------------------------------------------------------------------------------
# risk
# --------------------------------------------------------------------------------------


def _jsonable(value: Any) -> Any:
    return value.isoformat() if isinstance(value, date) else value


async def _load_risk(db: AsyncSession, proposal: Proposal) -> Risk:
    risk = await db.get(Risk, proposal.target_id)
    if risk is None:
        raise ProposalTargetInvalid(
            f"Risk {proposal.target_id} no longer exists, so there is nothing to apply "
            "this to."
        )
    return risk


async def _observe_risk(db: AsyncSession, proposal: Proposal) -> Any:
    risk = await db.get(Risk, proposal.target_id)
    if risk is None:
        return None
    return _jsonable(getattr(risk, proposal.field_path, None))


async def _apply_risk(
    db: AsyncSession,
    proposal: Proposal,
    *,
    value: Any,
    actor: str,
    proposal_id: int,
) -> None:
    field = proposal.field_path
    if field not in APPLIABLE_RISK_FIELDS:
        raise ProposalTargetInvalid(
            f"{field!r} is not a risk field a proposal may set. Settable: "
            + ", ".join(sorted(APPLIABLE_RISK_FIELDS))
            + "."
        )

    risk = await _load_risk(db, proposal)
    before = snapshot(risk)
    setattr(risk, field, value)

    # The same rescoring the PATCH route runs. Written out rather than imported from the
    # route module because importing a router into a service inverts the dependency and
    # drags FastAPI into the applier; if this ever grows a third caller it belongs in
    # ``models/matrix.py`` beside the functions it calls.
    risk.impact = overall_impact(risk.impact_scores, risk.impact)
    risk.target_impact = overall_impact(risk.target_impact_scores, risk.target_impact)
    config = await get_active_config(db)
    risk.risk_level = band_for(risk.probability, risk.impact, config)
    risk.target_risk_level = band_for(
        risk.target_probability, risk.target_impact, config
    )

    after = snapshot(risk)
    changes = [
        {"field": f, "old": before.get(f), "new": after.get(f)}
        for f in before
        if before.get(f) != after.get(f)
    ]
    if not changes:
        # An accept that changes nothing is still a disposition worth recording — a
        # reviewer agreeing with what is already there is a real signal — but writing an
        # empty history row would put noise in the register's audit trail. The ledger
        # holds the record instead.
        return

    db.add(
        RiskHistory(
            risk_id=risk.id,
            risk_code=risk.risk_code,
            action="updated",
            actor=actor,
            changes=changes,
            provenance=f"proposal:{proposal_id}",
        )
    )


async def _create_risk(
    db: AsyncSession,
    proposal: Proposal,
    *,
    value: Any,
    actor: str,
    proposal_id: int,
) -> int:
    """Materialise a draft risk into the register. Returns the new risk id.

    Writes through the same steps ``POST /risks`` does — resolve the subcategory, allocate
    a code with :func:`services.risk_code.next_code`, rescore, write a ``created`` history
    row — with ``provenance`` set. Repeated here rather than imported from the route for
    the reason the update applier gives: importing a router into a service inverts the
    dependency and drags FastAPI into the applier.

    **The scope comes from the proposal, never from the payload.** A generator names the
    project by raising the proposal against it, and a payload that could also name one
    would give an accepted suggestion a way to land a risk in a project nobody was looking
    at. The proposal's scope is set by the route from the authenticated request, and this
    reads that.

    **No probability, no impact, no status.** See :data:`CREATABLE_RISK_FIELDS`. The row
    lands unassessed, which is what a risk that has been identified and not yet elicited
    actually is, and the register already renders that state.
    """
    if not isinstance(value, dict):
        raise ProposalTargetInvalid(
            "A risk creation payload must be an object carrying at least "
            f"{' and '.join(REQUIRED_RISK_FIELDS)}."
        )

    unknown = sorted(set(value) - CREATABLE_RISK_FIELDS)
    if unknown:
        raise ProposalTargetInvalid(
            f"{', '.join(unknown)} cannot be set when creating a risk from a proposal. "
            "Settable: " + ", ".join(sorted(CREATABLE_RISK_FIELDS)) + "."
        )

    missing = [f for f in REQUIRED_RISK_FIELDS if not str(value.get(f) or "").strip()]
    if missing:
        raise ProposalTargetInvalid(
            "A risk creation payload is missing " + ", ".join(missing) + "."
        )

    scope = await db.get(ScopeNode, proposal.scope_id)
    if scope is None:
        raise ProposalTargetInvalid(
            f"Scope {proposal.scope_id} no longer exists, so the risk has nowhere to land."
        )
    if scope.kind != OWNING_KIND:
        raise ProposalTargetInvalid(
            f"{scope.name!r} is a {scope.kind}, and risks are authored on projects."
        )

    prefix = str(value["subcategory_prefix"]).strip().upper()
    row = (
        await db.execute(
            select(RbsCategory, RbsSubcategory)
            .join(RbsSubcategory, RbsSubcategory.category_id == RbsCategory.id)
            .where(
                RbsCategory.code == prefix.split("-")[0],
                RbsSubcategory.code == prefix.split("-")[-1],
            )
        )
    ).first()
    if row is None or prefix.count("-") != 1:
        raise ProposalTargetInvalid(
            f"{prefix!r} is not a subcategory in this install's RBS, so the risk cannot "
            "be filed."
        )
    category, subcategory = row

    seq, risk_code = await next_code(db, scope)
    risk = Risk(
        scope_id=scope.id,
        subcategory_id=subcategory.id,
        seq=seq,
        risk_code=risk_code,
        title=str(value["title"]).strip(),
        description=_clean(value.get("description")),
        causes=_clean(value.get("causes")),
        consequences=_clean(value.get("consequences")),
        status="Open",
    )
    # Rescored even though nothing here sets a score: ``band_for`` with no probability
    # returns no band, and running it anyway means one code path decides what an
    # unassessed risk looks like instead of two.
    risk.impact = overall_impact(risk.impact_scores, risk.impact)
    risk.target_impact = overall_impact(risk.target_impact_scores, risk.target_impact)
    config = await get_active_config(db)
    risk.risk_level = band_for(risk.probability, risk.impact, config)
    risk.target_risk_level = band_for(
        risk.target_probability, risk.target_impact, config
    )

    db.add(risk)
    await db.flush()

    changes = creation_changes(snapshot(risk))
    changes.append(
        {
            "field": "subcategory",
            "old": None,
            "new": f"{category.code}-{subcategory.code}",
        }
    )
    db.add(
        RiskHistory(
            risk_id=risk.id,
            risk_code=risk.risk_code,
            action="created",
            actor=actor,
            changes=changes,
            provenance=f"proposal:{proposal_id}",
        )
    )
    return risk.id


def _clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


register("risk", _apply_risk, _observe_risk)
register_creator("risk", _create_risk)
