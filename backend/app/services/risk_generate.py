"""Running one risk-identification pass and turning what comes back into proposals.

This is the first place in the platform where a model's output reaches a database, and the
whole of P5 was built in the order it was so that this module could be short and boring.
It loads a corpus, walks it in windows, asks a model what could go wrong, refuses anything
ungrounded or already known, and writes what survives to the proposal ledger. It writes
nothing else. Invariant 4 holds here not by discipline but by construction: the only write
path out of this module is ``proposal_ledger.propose``, and that table has no route into
``risk`` that does not pass through a human disposition.

**Never raises for a bad answer; raises for a broken deployment.** A window whose response
was unparseable, a candidate that cited a chunk it was never shown, a model that returned
prose — all of those are recorded on the run and the pass continues. A missing provider, an
empty corpus or an RBS with no subcategories fails the run before any call is made, because
each is something a person fixes rather than something a rerun would resolve.

**Precedent, not suppression, is the default answer to overlap.** The register is loaded
once and every candidate is measured against it. A near-copy is dropped and reported; a
merely related risk is *kept*, with the existing risk attached as a second citation, so the
reviewer decides whether they are the same thing. The asymmetry is argued in
``agents/dedupe.py``: a false suppression is invisible and permanent, a false pass costs
four seconds.

**Everything approximate is declared on the run.** Windows truncated by the cap, candidates
refused and why, the exact pack that was sent, the raw text that came back. A run that read
six documents out of nine and says so is a result. One that does not is a wrong answer
wearing the clothes of a right one.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import dedupe
from app.agents import risk_id as agent
from app.agents.types import (
    ALREADY_IN_REGISTER,
    DUPLICATE_IN_BATCH,
    Candidate,
    Drop,
    PackChunk,
    TaxonomyEntry,
    Window,
)
from app.core.config import Settings, get_settings
from app.core.errors import GenerationNotRunnable, LlmError
from app.llm import Message, Provider, get_provider
from app.llm.types import USER
from app.models.document import ACTIVE, Document, DocumentChunk
from app.models.generation import FAILED, RUNNING, SUCCEEDED, GenerationRun
from app.models.rbs import RbsCategory, RbsSubcategory
from app.models.risk import Risk
from app.models.scope import ScopeNode
from app.services import proposal_ledger
from app.services.evidence import DOC, HISTORY, EXCERPT_CHARS
from app.services.mapping_suggest import tokenize

__all__ = ["execute", "record_failure", "load_pack", "TARGET_TYPE", "FIELD_PATH"]

#: The ledger's ``target_type`` for a draft risk. The same string the update applier uses,
#: because an accepted creation and an accepted edit both end up on the same table and a
#: second vocabulary for that would give the inbox two names for one thing.
TARGET_TYPE = "risk"

#: ``*`` — the whole row. A creation addresses no single field.
FIELD_PATH = "*"


async def execute(
    db: AsyncSession,
    run_id: int,
    *,
    settings: Settings | None = None,
    provider: Provider | None = None,
) -> GenerationRun | None:
    """Run one queued generation to completion.

    Mirrors ``sim_execute.execute`` deliberately, including the prologue that records an
    infrastructure failure before re-raising it: a run left sitting in ``queued`` with
    nothing written against it is unreachable — not running, not failed, not restartable —
    and that is the failure mode worth spending a paragraph to prevent.

    ``provider`` and ``settings`` are injectable for the same reason ``sim_assembly``
    takes its inputs rather than fetching them: a test that wants to see what this does
    with a truncated response, or a model that cites a chunk it was never shown, has to be
    able to *supply* that response. Production passes neither and the registry decides.
    """
    config = settings or get_settings()

    try:
        run = await db.get(GenerationRun, run_id)
        if run is None:
            return None
        if run.status in {SUCCEEDED, FAILED}:
            return run
        run.status = RUNNING
        run.started_at = _now()
        await db.commit()
    except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
        await record_failure(
            db,
            run_id,
            "The run could not be started. The worker reached the database but could "
            f"not claim the run: {type(exc).__name__}: {exc}",
        )
        raise

    try:
        await _generate(db, run, provider=provider or get_provider(config), config=config)
    except (GenerationNotRunnable, LlmError) as exc:
        # Both are outcomes rather than crashes: nothing to read, nothing configured, the
        # provider refused. Recorded on the run's face, where the person who dispatched it
        # will look, instead of in a worker log they cannot reach.
        await _finish(db, run, status=FAILED, error=str(exc))
        return run
    except Exception as exc:  # noqa: BLE001 - a generator must say why, not disappear
        await _finish(
            db,
            run,
            status=FAILED,
            error=f"The generation failed: {type(exc).__name__}: {exc}",
        )
        raise

    return run


async def record_failure(db: AsyncSession, run_id: int, message: str) -> bool:
    """Mark a run failed from outside the normal path. ``False`` if it was already done."""
    run = await db.get(GenerationRun, run_id)
    if run is None or run.status in {SUCCEEDED, FAILED}:
        return False
    await _finish(db, run, status=FAILED, error=message)
    return True


# --------------------------------------------------------------------------------------
# the pass
# --------------------------------------------------------------------------------------


async def _generate(
    db: AsyncSession,
    run: GenerationRun,
    *,
    provider: Provider,
    config: Settings,
) -> None:
    scope = await db.get(ScopeNode, run.scope_id)
    project_name = scope.name if scope is not None else "this project"

    chunks, document_ids, doc_ids_seen = await load_pack(
        db, run.scope_id, only_documents=run.document_ids
    )
    if not chunks:
        raise GenerationNotRunnable(
            "There is nothing in this project's corpus to read. Upload or paste a "
            "document first — a risk identification pass with no documents would be a "
            "model inventing risks, which is the one thing this stage must not do."
        )

    taxonomy = await _taxonomy(db)
    if not taxonomy:
        raise GenerationNotRunnable(
            "The risk breakdown structure has no subcategories, so nothing the model "
            "found could be filed. Seed the RBS first."
        )
    known_prefixes = frozenset(entry.prefix for entry in taxonomy)

    windows, truncated = agent.build_windows(
        chunks,
        document_ids=document_ids,
        max_chars=config.generation_window_chars,
        max_windows=config.generation_max_windows,
    )

    run.provider = provider.name
    run.document_ids = sorted(doc_ids_seen)
    run.chunk_count = len(chunks)
    run.window_count = len(windows)
    run.windows_truncated = truncated
    run.pack_sha256 = _fingerprint(windows)
    await db.commit()

    register = await _register_tokens(db, run.scope_id)
    excerpts = {chunk.ref: chunk.text for chunk in chunks}

    candidates: list[Candidate] = []
    drops: list[Drop] = []
    transcript: list[dict] = []
    input_tokens = 0
    output_tokens = 0
    model_name = ""

    for index, window in enumerate(windows):
        content = agent.build_messages(window, taxonomy, project_name=project_name)
        completion = await provider.complete(
            system=agent.SYSTEM_PROMPT,
            messages=[Message(role=USER, content=content)],
            max_tokens=config.llm_max_output_tokens,
            temperature=config.llm_temperature,
        )
        model_name = completion.model or model_name
        input_tokens += completion.input_tokens or 0
        output_tokens += completion.output_tokens or 0

        kept, window_drops = agent.parse(
            completion.text,
            allowed_refs=window.refs,
            known_prefixes=known_prefixes,
        )
        candidates.extend(kept)
        drops.extend(window_drops)

        transcript.append(
            {
                "window": index,
                "document_id": window.document_id,
                "document": window.document_label,
                "chunk_refs": [c.ref for c in window.chunks],
                "prompt_sha256": _sha(content),
                "response": completion.text[: config.generation_transcript_chars],
                "response_truncated_in_transcript": (
                    len(completion.text) > config.generation_transcript_chars
                ),
                "stop_reason": completion.stop_reason,
                "hit_output_ceiling": completion.truncated,
                "input_tokens": completion.input_tokens,
                "output_tokens": completion.output_tokens,
                "kept": len(kept),
                "dropped": [d.as_dict() for d in window_drops],
            }
        )

    run.candidate_count = len(candidates) + len(
        [d for d in drops if d.raw is not None]
    )
    survivors, dedupe_drops, precedents = _dedupe(candidates, register)
    drops.extend(dedupe_drops)

    raised = 0
    for candidate in survivors:
        await proposal_ledger.propose(
            db,
            scope_id=run.scope_id,
            target_type=TARGET_TYPE,
            target_id=None,
            field_path=FIELD_PATH,
            proposed_value=_payload(candidate),
            rationale=candidate.rationale,
            evidence_refs=_evidence(candidate, excerpts, precedents.get(id(candidate))),
            confidence=candidate.confidence,
            generator_model=model_name or run.model,
            generator_prompt_version=agent.PROMPT_VERSION,
            generation_run_id=run.id,
        )
        raised += 1

    run.model = model_name or run.model
    run.proposal_count = raised
    run.dropped = [d.as_dict() for d in drops] or None
    run.transcript = transcript or None
    run.input_tokens = input_tokens or None
    run.output_tokens = output_tokens or None
    await _finish(db, run, status=SUCCEEDED, error=None)


# --------------------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------------------


async def load_pack(
    db: AsyncSession, scope_id: int, *, only_documents: list[int] | None = None
) -> tuple[list[PackChunk], list[int], set[int]]:
    """Every active chunk in scope, in document then ordinal order.

    Active only. A withdrawn document stays citable — that is why 5.2 withdraws rather than
    deletes — and stops being *cited*, so a pass run after somebody retires a superseded
    drawing register does not go on finding risks in it.

    Ordered rather than arbitrary, because the windowing that follows depends on adjacent
    chunks being adjacent in the document. An unordered scan would produce windows whose
    contents are a coherent size and an incoherent read.
    """
    stmt = (
        select(DocumentChunk, Document)
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(Document.scope_id == scope_id, Document.status == ACTIVE)
        .order_by(DocumentChunk.document_id, DocumentChunk.ordinal)
    )
    if only_documents:
        stmt = stmt.where(Document.id.in_(only_documents))

    rows = (await db.execute(stmt)).all()
    chunks: list[PackChunk] = []
    document_ids: list[int] = []
    seen: set[int] = set()
    for chunk, document in rows:
        label = document.title or document.filename
        chunks.append(
            PackChunk(
                ref=f"{DOC}:{chunk.id}",
                text=chunk.text,
                section=chunk.section,
                locator=chunk.locator,
                document_label=label,
            )
        )
        document_ids.append(document.id)
        seen.add(document.id)
    return chunks, document_ids, seen


async def _taxonomy(db: AsyncSession) -> list[TaxonomyEntry]:
    rows = (
        await db.execute(
            select(RbsCategory, RbsSubcategory)
            .join(RbsSubcategory, RbsSubcategory.category_id == RbsCategory.id)
            .order_by(
                RbsCategory.sort_order,
                RbsCategory.code,
                RbsSubcategory.sort_order,
                RbsSubcategory.code,
            )
        )
    ).all()
    return [
        TaxonomyEntry(
            prefix=f"{category.code}-{sub.code}",
            category_name=category.name,
            name=sub.name,
        )
        for category, sub in rows
    ]


async def _register_tokens(
    db: AsyncSession, scope_id: int
) -> list[tuple[str, str, tuple[str, ...]]]:
    """The project's existing register, tokenised once, as ``(ref, code, tokens)``.

    This project only, unlike the evidence service's reference-class search which spans the
    hierarchy on purpose. The question here is "do we already have this one", and a
    sibling project having a similar risk is not a reason to suppress it on this one — it
    is a reason to raise it, which is the opposite conclusion.
    """
    risks = list(
        await db.scalars(select(Risk).where(Risk.scope_id == scope_id))
    )
    out: list[tuple[str, str, tuple[str, ...]]] = []
    for risk in risks:
        text = " ".join(
            part
            for part in (risk.title, risk.description, risk.causes, risk.consequences)
            if part
        )
        out.append((f"{HISTORY}:{risk.id}", risk.risk_code, tuple(tokenize(text))))
    return out


# --------------------------------------------------------------------------------------
# deduplication and payloads
# --------------------------------------------------------------------------------------


def _statement_tokens(candidate: Candidate) -> tuple[str, ...]:
    return tuple(
        tokenize(
            " ".join(
                (candidate.title, candidate.cause, candidate.event, candidate.effect)
            )
        )
    )


def _dedupe(
    candidates: list[Candidate],
    register: list[tuple[str, str, tuple[str, ...]]],
) -> tuple[list[Candidate], list[Drop], dict[int, dedupe.Match]]:
    """Suppress repeats, keep the merely related, and say which is which.

    Two passes in one loop, register first. A candidate that duplicates an existing risk is
    dropped whether or not the batch also repeated it, and reporting it as a batch duplicate
    would tell the reviewer the less useful of the two true facts.
    """
    kept: list[Candidate] = []
    drops: list[Drop] = []
    precedents: dict[int, dedupe.Match] = {}
    seen: list[tuple[str, str, tuple[str, ...]]] = []

    for position, candidate in enumerate(candidates):
        tokens = _statement_tokens(candidate)

        existing = dedupe.best_match(tokens, register)
        if existing is not None and existing.suppresses:
            drops.append(
                Drop(
                    ALREADY_IN_REGISTER,
                    f"{existing.label} already covers this "
                    f"({existing.score:.0%} of the wording is shared).",
                    raw=_payload(candidate),
                )
            )
            continue

        twin = dedupe.best_match(tokens, seen, floor=dedupe.SUPPRESS_AT)
        if twin is not None:
            drops.append(
                Drop(
                    DUPLICATE_IN_BATCH,
                    f"The same finding was already raised in this pass as "
                    f"{twin.label} ({twin.score:.0%} shared).",
                    raw=_payload(candidate),
                )
            )
            continue

        if existing is not None and existing.is_precedent:
            precedents[id(candidate)] = existing

        seen.append((str(position), candidate.title[:80], tokens))
        kept.append(candidate)

    return kept, drops, precedents


def _payload(candidate: Candidate) -> dict:
    """The creation payload, in exactly the shape ``proposal_apply`` will accept.

    ``description`` is the assembled cause-event-effect sentence and ``causes`` /
    ``consequences`` are the parts, so the register shows a readable statement *and* keeps
    the three components separately editable. Writing only the sentence would make the
    first analyst who wants to change the effect rewrite the whole thing.
    """
    return {
        "subcategory_prefix": candidate.subcategory_prefix,
        "title": candidate.title,
        "description": candidate.statement(),
        "causes": candidate.cause,
        "consequences": candidate.effect,
    }


def _evidence(
    candidate: Candidate,
    excerpts: dict[str, str],
    precedent: dedupe.Match | None,
) -> list[dict]:
    """``[{kind, ref, excerpt}]`` — the ledger's shape, and the evidence service's.

    Built directly rather than through ``evidence.search`` because a sweep is not a query:
    the chunks a candidate may cite are the ones it was shown, and re-retrieving them by
    keyword could only find a different set. The ``kind`` strings are imported from
    ``services/evidence.py`` rather than written out, so a stored ref that stops resolving
    is a broken import instead of a silent mismatch.
    """
    refs = [
        {"kind": DOC, "ref": ref, "excerpt": _excerpt(excerpts.get(ref, ""))}
        for ref in candidate.evidence_refs
    ]
    if precedent is not None:
        refs.append(
            {
                "kind": HISTORY,
                "ref": precedent.key,
                "excerpt": (
                    f"{precedent.label} in this register overlaps this finding "
                    f"({precedent.score:.0%} of the wording is shared). Merge it, or "
                    "keep both if they are genuinely different risks."
                ),
            }
        )
    return refs


def _excerpt(text: str) -> str:
    text = text.strip()
    if len(text) <= EXCERPT_CHARS:
        return text
    window = text[:EXCERPT_CHARS]
    cut = window.rfind(" ")
    return (window[:cut] if cut > EXCERPT_CHARS // 2 else window).rstrip() + "…"


# --------------------------------------------------------------------------------------
# bookkeeping
# --------------------------------------------------------------------------------------


def _fingerprint(windows: list[Window]) -> str:
    """Hash of the extracts actually sent, in order.

    Not a seed and not claimed as one. Two runs sharing this value read exactly the same
    material, which makes their outputs comparable; it does not make them equal, because no
    temperature setting makes a model deterministic across time and deployments.
    """
    payload = json.dumps(
        [[chunk.ref for chunk in window.chunks] for window in windows],
        separators=(",", ":"),
    )
    return _sha(payload)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _finish(
    db: AsyncSession, run: GenerationRun, *, status: str, error: str | None
) -> None:
    run.status = status
    run.error = error
    run.finished_at = _now()
    await db.commit()


def _now() -> datetime:
    return datetime.now(timezone.utc)
