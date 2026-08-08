"""The proposal ledger: everything generated, before a human has ruled on it.

**One table, not one per subsystem.** A proposal addresses its target by
``(target_type, target_id, field_path)`` rather than by a foreign key, which costs
referential integrity to the target and buys the thing this table exists for: "a human can
intervene at every step" becomes a property of the architecture instead of nine separate UI
affordances that each have to be remembered, tested, and audited. A per-subsystem
``risk_suggestion`` / ``estimate_suggestion`` / ``mapping_suggestion`` split would give the
database its foreign keys and give the reviewer nine inboxes, nine status vocabularies, and
no single answer to "what did the model decide today". The integrity that is lost is
recovered where it is actually needed — the applier resolves the target and refuses an
unknown one — and the audit answer is worth more here than the constraint.

**Nothing generated writes a domain table directly** (invariant 4). A generator writes a
row here; a human disposes; the applier writes the domain row. There is no second path, and
the absence of one is what makes the ledger trustworthy rather than decorative.

**Evidence is mandatory, confidence is not.** ``evidence_refs`` carries at least one
reference and the constraint is in the database, not only in the Pydantic layer, because a
suggestion with nothing behind it is the exact failure mode this subsystem exists to
prevent — a fluent sentence that a reviewer accepts because it sounds like the others.
``confidence`` is nullable and NULL means the generator abstained. Zero would be a claim.

**Terminal is terminal.** Nothing transitions out of ``accepted``, ``edited``, ``rejected``
or ``superseded``, there is no delete route, and a disposition is never rewritten. Same
posture as simulation runs (invariant 6) and the history tables (invariant 5): the record
of what was decided has to outlive anyone's opinion about whether it was right.

**Park is a flag, not a status.** A parked proposal is still awaiting disposition, just not
this week. Making it a sixth status would put a non-terminal value into a vocabulary whose
whole value is that four of its five members are final.

**Merge is supersession with a pointer.** Merging A into B disposes A as ``superseded``
with ``superseded_by = B``. Semantic merging of draft-risk *content* — two agents finding
the same risk in different words — belongs to the workshop agent, which merges the risks;
the ledger only records that one suggestion was subsumed by another.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    false,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

#: The partial-index predicate, written once so the model and migration 0021 cannot drift.
#: Both dialects parse it identically, which is why it is a literal rather than a
#: constructed expression.
PENDING_PREDICATE = "status = 'pending' AND target_id IS NOT NULL"

#: Awaiting a human. The only status a disposition may act on.
PENDING = "pending"

#: Applied exactly as proposed.
ACCEPTED = "accepted"

#: Applied with the human's own value. ``applied_value`` differs from ``proposed_value``
#: and the delta between them is the signal a future ranking pass learns from.
EDITED = "edited"

#: Not applied. Requires a note — the reason is half the signal.
REJECTED = "rejected"

#: Replaced by a newer proposal for the same target and field, or merged into another.
SUPERSEDED = "superseded"

STATUSES: tuple[str, ...] = (PENDING, ACCEPTED, EDITED, REJECTED, SUPERSEDED)

#: Once here, a proposal is read-only forever.
TERMINAL: frozenset[str] = frozenset({ACCEPTED, EDITED, REJECTED, SUPERSEDED})

#: What a caller may ask for. ``merge`` and ``park``/``unpark`` are verbs the service
#: translates; ``superseded`` is never requested directly.
ACTIONS: tuple[str, ...] = ("accept", "edit", "reject", "merge")


class Proposal(Base):
    """One generated suggestion, and the record of what a human did about it."""

    __tablename__ = "proposal"

    id: Mapped[int] = mapped_column(primary_key=True)

    #: ``RESTRICT``, matching every other scoped table: deleting a project must not take
    #: the record of what was proposed about it. The API refuses the delete instead.
    scope_id: Mapped[int] = mapped_column(
        ForeignKey("scope_node.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    #: Which kind of row this is about — ``risk``, ``risk_quant_estimate``,
    #: ``risk_activity_mapping``, and later ``draft_risk``. Deliberately a string and not
    #: an enum column: the set grows once per P5 stage, and a CHECK here would mean a
    #: migration every time a new generator ships. The applier registry is the real
    #: gatekeeper, and it fails loudly on a type it does not know.
    target_type: Mapped[str] = mapped_column(String(40), nullable=False)

    #: ``NULL`` means the proposal *creates* a row rather than changing one. Creation
    #: proposals are exempt from the one-pending-per-field rule below, because there is no
    #: field yet for two of them to collide on.
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: Dotted path into the target, or ``*`` for the whole row. ``cost.ml`` addresses the
    #: nested elicitation payload the quant route already speaks, so an applier can hand
    #: the value to the existing ``PUT`` path rather than growing a second write path with
    #: its own validation.
    field_path: Mapped[str] = mapped_column(String(120), nullable=False)

    proposed_value: Mapped[Any] = mapped_column(JSON, nullable=False)

    #: The target's value when the proposal was drafted. Kept so the disposition endpoint
    #: can tell an accept that would silently overwrite a newer human edit from one that
    #: would not. Without it, the race is invisible and resolves in the model's favour.
    observed_value: Mapped[Any] = mapped_column(JSON, nullable=True)

    rationale: Mapped[str] = mapped_column(Text, nullable=False)

    #: ``[{kind, ref, excerpt}]``. ``kind`` stays a free string until the evidence service
    #: defines the source set; pinning it now would be a guess that migration has to undo.
    evidence_refs: Mapped[list] = mapped_column(JSON, nullable=False)

    #: NULL means the generator abstained. Never zero — see the module docstring.
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    #: Two columns rather than a ``generator`` JSON blob: every question anyone asks of
    #: this field is a GROUP BY ("which prompt version gets edited most"), and a blob
    #: answers it only with a JSON extraction in every query.
    generator_model: Mapped[str] = mapped_column(String(120), nullable=False)
    generator_prompt_version: Mapped[str] = mapped_column(String(40), nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=PENDING, server_default=PENDING, index=True
    )

    parked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    #: Set on accept (equal to ``proposed_value``) and on edit (different). NULL on
    #: reject and supersede, where nothing was applied.
    applied_value: Mapped[Any] = mapped_column(JSON, nullable=True)

    superseded_by: Mapped[int | None] = mapped_column(
        ForeignKey("proposal.id", ondelete="RESTRICT"), nullable=True
    )

    disposed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    disposed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    disposition_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'accepted', 'edited', 'rejected', 'superseded')",
            name="ck_proposal_status",
        ),
        # In the database rather than only at the Pydantic boundary, because the boundary
        # is bypassed by every generator that writes through the service directly, and
        # "at least one piece of evidence" is the rule the whole subsystem rests on.
        # ``json_array_length`` is present in SQLite's built-in JSON1 and in Postgres for
        # the ``json`` type that ``sa.JSON`` renders to, so one expression covers both.
        CheckConstraint(
            "json_array_length(evidence_refs) >= 1", name="ck_proposal_has_evidence"
        ),
        # One pending proposal per target field. A second generator pass produces a fresh
        # suggestion rather than a duplicate inbox entry, and the service supersedes the
        # older one inside the same transaction. Partial, so the terminal rows a target
        # accumulates over its life do not collide with each other.
        Index(
            "uq_proposal_one_pending_per_field",
            "target_type",
            "target_id",
            "field_path",
            unique=True,
            sqlite_where=text(PENDING_PREDICATE),
            postgresql_where=text(PENDING_PREDICATE),
        ),
        Index("ix_proposal_target", "target_type", "target_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Proposal {self.id} {self.target_type}."
            f"{self.field_path} {self.status}>"
        )


class EvidenceRef(BaseModel):
    """One pointer at the thing a suggestion came from.

    ``ref`` is opaque to the ledger — a document chunk id, an activity code, a run id —
    and is resolved by whatever produced it. ``excerpt`` is optional and exists so a
    reviewer can judge a suggestion without leaving the inbox; a citation nobody can read
    without three clicks is a citation nobody reads.
    """

    kind: str = Field(..., max_length=40)
    ref: str = Field(..., max_length=500)
    excerpt: str | None = None


class ProposalRead(BaseModel):
    id: int
    scope_id: int
    target_type: str
    target_id: int | None
    field_path: str
    proposed_value: Any
    observed_value: Any
    rationale: str
    evidence_refs: list[EvidenceRef]
    confidence: float | None
    generator_model: str
    generator_prompt_version: str
    status: str
    parked: bool
    applied_value: Any
    superseded_by: int | None
    disposed_by: str | None
    disposed_at: datetime | None
    disposition_note: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
