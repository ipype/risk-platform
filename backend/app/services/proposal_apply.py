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

Only the ``risk`` applier ships in 5.1. Estimates and mappings arrive with the generators
that propose them, so their appliers can be written against a real payload rather than an
imagined one.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ProposalTargetInvalid
from app.models.history import RiskHistory, snapshot
from app.models.matrix import band_for, get_active_config, overall_impact
from app.models.proposal import Proposal
from app.models.risk import Risk

__all__ = ["apply", "observe", "register", "APPLIABLE_RISK_FIELDS"]

Applier = Callable[..., Awaitable[None]]
Observer = Callable[..., Awaitable[Any]]

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

_APPLIERS: dict[str, Applier] = {}
_OBSERVERS: dict[str, Observer] = {}


def register(target_type: str, applier: Applier, observer: Observer) -> None:
    """Wire a target type into the ledger. Called at import time by this module."""
    _APPLIERS[target_type] = applier
    _OBSERVERS[target_type] = observer


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
        # Creation proposals record a decision in 5.1 and materialise later, with the
        # draft-risk pipeline that will produce them. Applying one now would mean
        # inventing a risk code, a scope handoff and a subcategory from a payload no
        # generator writes yet.
        raise ProposalTargetInvalid(
            "This proposal creates a new row rather than changing one, and creation is "
            "not materialised yet. Accepting it would record a decision with nothing "
            "behind it."
        )
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


register("risk", _apply_risk, _observe_risk)
