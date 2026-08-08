"""One pass of a generator over a corpus: what it read, what it said, what survived.

**A run exists so a batch of proposals has a parent.** Without one, an inbox holding
forty rows can answer "who suggested this" only per row, and cannot answer the questions a
reviewer actually asks first: how many did it offer, how many were refused before I saw
them, what did it read, and can I dismiss the whole batch. Those are properties of the
pass, so they live on a row of its own.

**Append-only, like simulation runs.** No delete route, no re-run in place, no editing a
finished run's counts. The reason is the same one invariant 5 and 6 give: the record of
what was produced, and of how much of it was refused on the way, is the evidence that the
review process is real. A run that can be tidied up afterwards is not evidence of anything.

**Reproducible is not the claim being made.** A simulation run stores a seed and reproduces
bit for bit; a model call does not, at any temperature. So this table stores something
weaker and more honest: the exact prompt version, provider, model, temperature and a
fingerprint of the extract pack that was sent, plus the raw text that came back. That makes
a run *auditable* — anyone can see what was asked and what was answered — without claiming
it is *replayable*. Stating which of the two you have is the point; the failure mode worth
avoiding is a column called ``seed`` on a row that cannot use one.

**The transcript holds what the model said, not what we made of it.** Candidates that were
refused are in ``dropped`` with their reason. Both are kept because the interesting quality
signal in this stage is the ratio: eleven offered and four ungrounded is a prompt problem,
eleven offered and four already in the register is the system working.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
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
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

#: The only generator in 5.4. A string rather than an enum column for the same reason
#: ``proposal.target_type`` is one: the set grows once per P5 stage, and a CHECK here
#: would mean a migration every time a generator ships.
RISK_IDENTIFICATION = "risk_identification"

QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"

#: No ``cancelled``. Cancel is a real feature for a run that fires twenty paid calls, and
#: it is not this delivery's — adding the status now would put a value in the vocabulary
#: that nothing can set and no reviewer can act on. It arrives as its own migration, the
#: way ``simulation_run`` got one in 0018.
STATUSES: tuple[str, ...] = (QUEUED, RUNNING, SUCCEEDED, FAILED)

TERMINAL: frozenset[str] = frozenset({SUCCEEDED, FAILED})


class GenerationRun(Base):
    """One dispatch of one generator against one scope."""

    __tablename__ = "generation_run"

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_genrun_status",
        ),
        Index("ix_genrun_scope_created", "scope_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    #: ``RESTRICT``, matching every other scoped table. Deleting a project must not take
    #: the record of what was generated about it.
    scope_id: Mapped[int] = mapped_column(
        ForeignKey("scope_node.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    kind: Mapped[str] = mapped_column(
        String(40), nullable=False, default=RISK_IDENTIFICATION
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=QUEUED, server_default=QUEUED, index=True
    )

    # -- how it was asked ----------------------------------------------------------
    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    temperature: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # -- what it read --------------------------------------------------------------
    #: Document ids in the pack. A list rather than a join table: it is written once,
    #: read whole, and never queried across runs. A join table would be three more
    #: objects for a question nobody asks.
    document_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    window_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: The corpus had more windows than the cap allowed. On the face of the run, because
    #: "no risks found in the drainage report" means something different when the drainage
    #: report was never read.
    windows_truncated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    #: Fingerprint of the extracts actually sent, in order. Two runs with the same value
    #: read exactly the same thing, which is the closest this stage gets to a seed.
    pack_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # -- what came of it -----------------------------------------------------------
    #: Everything the model offered, before any of it was refused.
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Rows actually raised in the ledger. The gap between this and ``candidate_count`` is
    #: the whole quality signal of the pass.
    proposal_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: ``[{reason, detail, raw}]``. Never summarised to a count alone — a reviewer told
    #: that four were refused and not which four has been given a number, not a record.
    dropped: Mapped[list | None] = mapped_column(JSON, nullable=True)
    #: Per-window: prompt fingerprint, the raw response, usage, parse outcome.
    transcript: Mapped[list | None] = mapped_column(JSON, nullable=True)

    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by: Mapped[str] = mapped_column(
        String(120), nullable=False, default="Unknown"
    )
    task_id: Mapped[str | None] = mapped_column(String(80), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<GenerationRun {self.id} {self.kind} {self.status}>"


class GenerationRunSummary(BaseModel):
    """List view. Deliberately without the transcript, which is megabytes at scale."""

    id: int
    scope_id: int
    kind: str
    status: str
    prompt_version: str
    provider: str
    model: str
    chunk_count: int
    window_count: int
    windows_truncated: bool
    candidate_count: int
    proposal_count: int
    error: str | None
    requested_by: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class GenerationRunDetail(GenerationRunSummary):
    temperature: float
    document_ids: list[int] | None
    pack_sha256: str | None
    dropped: list[dict] | None
    transcript: list[dict] | None
    input_tokens: int | None
    output_tokens: int | None
