"""Writing to and disposing of the proposal ledger.

Two entry points and one rule between them: :func:`propose` is the only way a generated
suggestion enters the system, and :func:`dispose` is the only way one leaves ``pending``.
Generators call the first, the API calls the second, and nothing calls the applier
directly — it runs inside :func:`dispose` so that a proposal cannot be marked accepted
without its value having actually landed.

**Neither function commits.** Both flush. The caller owns the transaction, which is what
lets an accept apply the value, write the audit row, and dispose the proposal in one unit
that either all happens or none of it does. A commit here would split that into three, and
the interesting failure — the applier raising after the disposition is written — is exactly
the one that must not be able to half-succeed.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    ProposalNotDisposable,
    ProposalStale,
    ProposalTargetInvalid,
)
from app.models.proposal import (
    ACCEPTED,
    EDITED,
    PENDING,
    REJECTED,
    SUPERSEDED,
    TERMINAL,
    Proposal,
)
from app.services import proposal_apply

__all__ = ["propose", "dispose", "set_parked"]


async def propose(
    db: AsyncSession,
    *,
    scope_id: int,
    target_type: str,
    target_id: int | None,
    field_path: str,
    proposed_value: object,
    rationale: str,
    evidence_refs: list[dict],
    generator_model: str,
    generator_prompt_version: str,
    confidence: float | None = None,
    observed_value: object = None,
    generation_run_id: int | None = None,
) -> Proposal:
    """Record a suggestion. Supersedes any pending one for the same target field.

    The supersession is automatic and silent by design: a generator re-run over a document
    set should refresh the inbox, not double it, and a reviewer who has not yet looked at
    yesterday's suggestion is better served by today's. The superseded row keeps its
    rationale and evidence, so nothing about the earlier attempt is lost — only its claim
    on the reviewer's attention.

    ``evidence_refs`` is not validated for shape here. The database enforces that there is
    at least one, and the Pydantic boundary enforces the rest; re-checking in the middle
    would put a third copy of the rule somewhere nobody thinks to update.

    **Creation proposals are not superseded.** ``target_id IS NULL`` means there is no
    field for two of them to collide on, so the query above is skipped and a second pass
    over the same corpus would double the inbox — which is exactly why the generator, not
    the ledger, is where deduplication lives. The ledger cannot tell that two draft risks
    written in different words are the same risk; the generator can, and it says so on the
    run rather than silently.
    """
    priors: list[Proposal] = []
    if target_id is not None:
        priors = list(
            await db.scalars(
                select(Proposal).where(
                    Proposal.status == PENDING,
                    Proposal.target_type == target_type,
                    Proposal.target_id == target_id,
                    Proposal.field_path == field_path,
                )
            )
        )
        for prior in priors:
            prior.status = SUPERSEDED
            prior.disposed_at = _now()
            prior.disposition_note = (
                "Superseded by a newer suggestion for the same field."
            )
        # Flushed *before* the insert below. SQLAlchemy's unit of work orders inserts
        # ahead of updates, so without this the new pending row and the old one exist
        # together for the length of one statement — which is exactly the window the
        # partial unique index is checked in.
        if priors:
            await db.flush()

    row = Proposal(
        scope_id=scope_id,
        target_type=target_type,
        target_id=target_id,
        field_path=field_path,
        proposed_value=proposed_value,
        observed_value=observed_value,
        rationale=rationale,
        evidence_refs=evidence_refs,
        confidence=confidence,
        generator_model=generator_model,
        generator_prompt_version=generator_prompt_version,
        status=PENDING,
        parked=False,
        generation_run_id=generation_run_id,
    )
    db.add(row)
    await db.flush()

    for prior in priors:
        prior.superseded_by = row.id
    if priors:
        await db.flush()
    return row


async def dispose(
    db: AsyncSession,
    proposal: Proposal,
    *,
    action: str,
    actor: str,
    applied_value: object = None,
    note: str | None = None,
    merge_into: int | None = None,
    confirm_stale: bool = False,
) -> Proposal:
    """Rule on a pending proposal, and apply it if the ruling says so.

    Applying happens *before* the status is written. If the applier raises — an unknown
    field path, a target that has since been deleted — the whole call fails and the
    proposal is still pending, which is the only honest outcome: a row marked accepted
    whose value never landed is worse than no ledger at all.
    """
    if proposal.status in TERMINAL:
        raise ProposalNotDisposable(proposal.id, proposal.status)

    if action == "reject":
        if not (note or "").strip():
            raise ProposalTargetInvalid(
                "A rejection needs a reason. It is the half of the signal that says what "
                "the model got wrong, and it is what a later ranking pass learns from."
            )
        proposal.status = REJECTED
    elif action == "merge":
        if merge_into is None:
            raise ProposalTargetInvalid("A merge needs the proposal it merges into.")
        other = await db.get(Proposal, merge_into)
        if other is None or other.id == proposal.id:
            raise ProposalTargetInvalid(
                f"Proposal {merge_into} is not something this one can merge into."
            )
        proposal.status = SUPERSEDED
        proposal.superseded_by = other.id
    elif action in ("accept", "edit"):
        value = proposal.proposed_value if action == "accept" else applied_value
        await _assert_fresh(db, proposal, confirm_stale=confirm_stale)
        await proposal_apply.apply(
            db, proposal, value=value, actor=actor, proposal_id=proposal.id
        )
        proposal.applied_value = value
        proposal.status = ACCEPTED if action == "accept" else EDITED
    else:
        raise ProposalTargetInvalid(
            f"{action!r} is not a disposition. Use accept, edit, reject or merge."
        )

    proposal.disposed_by = actor
    proposal.disposed_at = _now()
    proposal.disposition_note = note
    # Parking is about attention, and a disposed proposal has had it.
    proposal.parked = False
    await db.flush()
    return proposal


async def _assert_fresh(
    db: AsyncSession, proposal: Proposal, *, confirm_stale: bool
) -> None:
    """Refuse an accept that would overwrite an edit made since the proposal was drafted.

    Only checked when the generator recorded what it was looking at. A proposal with no
    ``observed_value`` makes no claim about the target's prior state, and inventing one
    now would fabricate a conflict or hide a real one depending on the guess.
    """
    if confirm_stale or proposal.observed_value is None or proposal.target_id is None:
        return
    current = await proposal_apply.observe(db, proposal)
    if current is not None and current != proposal.observed_value:
        raise ProposalStale(proposal.id, proposal.observed_value, current)


async def set_parked(db: AsyncSession, proposal: Proposal, *, parked: bool) -> Proposal:
    """Move a pending proposal in or out of the queue's foreground.

    Not audited in v1. Parking changes nothing about the record and nothing about the
    target; it is a reviewer's filing decision, and an event table for it would be the
    first thing written and the last thing read.
    """
    if proposal.status in TERMINAL:
        raise ProposalNotDisposable(proposal.id, proposal.status)
    proposal.parked = parked
    await db.flush()
    return proposal


def _now() -> datetime:
    return datetime.now(timezone.utc)
