"""Running one qualitative evaluation pass and turning what comes back into proposals.

The mirror image of ``risk_generate``. That one sweeps a corpus because there is no X to
ask about; this one has exactly one X per call — a risk already on the register — so it
retrieves through ``services/evidence.py`` and asks about what came back. It is the first
caller of that interface, which is what the interface was built for.

**Retrieval abstaining means no call is made.** This is the whole stage. A probability is a
number that looks the same whether it was reasoned from a document or produced to fill a
field, and it does not stay decorative: it multiplies into the matrix, the matrix drives
triage, and triage decides which risks get an expensive quantitative elicitation. So a
subject with nothing worth citing is recorded as skipped and never sent — the refusal
happens before the money is spent, not after the answer arrives.

**A field a person has already scored is never re-scored.** Not a flag, not a default that
can be turned off. Proposing against a judgement made in a workshop is the generator
arguing with the people who were in the room, and the ledger has no way to express "I think
you were wrong" that a reviewer would read as anything other than noise. An analyst who
wants a second opinion clears the field, which is one action and says what it means.

**The values a person did set are carried into the payload.** ``impact_scores`` is one JSON
column and the applier sets it whole, so a proposal holding only the two areas the model
judged would erase the three a person judged the moment it was accepted. The merge is
stated on the face of the proposal's rationale rather than left for someone to notice.

**Register comparables are declared for what they are.** Searching history across the
hierarchy is the single most useful thing this substrate does — four other projects carried
this risk and scored it 4 — and it is not a frequency. Those are other analysts'
judgements. The prompt says so, every rationale says so, and S11 is what turns them into
something else.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import qual_eval as agent
from app.agents.types import (
    ALREADY_ASSESSED,
    NO_EVIDENCE,
    SUBJECT_LIMIT,
    Assessment,
    Drop,
    EvidenceItem,
    ImpactArea,
    Level,
    RiskSubject,
    Scale,
    Skip,
)
from app.core.config import Settings, get_settings
from app.core.errors import GenerationNotRunnable, LlmError
from app.llm import Message, Provider, get_provider
from app.llm.types import USER
from app.models.generation import FAILED, RUNNING, SUCCEEDED, GenerationRun
from app.models.matrix import get_active_config
from app.models.rbs import RbsCategory, RbsSubcategory
from app.models.risk import Risk
from app.models.scope import ScopeNode
from app.services import proposal_ledger
from app.services.evidence import DOC, HISTORY, SCHEDULE, Evidence, search

__all__ = [
    "execute",
    "load_scale",
    "load_subjects",
    "TARGET_TYPE",
    "PROBABILITY_FIELD",
    "IMPACTS_FIELD",
    "SOURCES",
]

#: The ledger's ``target_type``. The same string identification uses, because both end up
#: on ``risk`` and a second vocabulary for one table would give the inbox two names for it.
TARGET_TYPE = "risk"

#: Two field paths, not one. A reviewer who agrees the cost impact is a 4 and thinks it is
#: less likely than the model does should not have to reject both halves to say so, and
#: the ledger's one-pending-per-field index then gives each half its own supersession on a
#: rerun — which creation proposals, carrying no target, never got.
PROBABILITY_FIELD = "probability"
IMPACTS_FIELD = "impact_scores"

#: All three substrates. Documents say what the project committed to, the register says
#: what comparable projects judged, and activity names say whether the work this risk
#: attaches to is even in the schedule. Dropping any one of them narrows the basis without
#: narrowing the question.
SOURCES: list[str] = [DOC, HISTORY, SCHEDULE]


async def execute(
    db: AsyncSession,
    run_id: int,
    *,
    settings: Settings | None = None,
    provider: Provider | None = None,
) -> GenerationRun | None:
    """Run one queued qualitative evaluation to completion.

    Structurally identical to ``risk_generate.execute``, including the prologue that
    records an infrastructure failure before re-raising it: a run left sitting in
    ``queued`` with nothing written against it is unreachable — not running, not failed,
    not restartable.
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
        from app.services.risk_generate import record_failure

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
        await _finish(db, run, status=FAILED, error=str(exc))
        return run
    except Exception as exc:  # noqa: BLE001 - a generator must say why, not disappear
        await _finish(
            db,
            run,
            status=FAILED,
            error=f"The evaluation failed: {type(exc).__name__}: {exc}",
        )
        raise

    return run


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

    scale = await load_scale(db)
    if not scale.areas or not scale.probability:
        raise GenerationNotRunnable(
            "The active risk matrix has no probability levels or no impact areas, so "
            "there is no scale to score against. Configure the matrix first."
        )

    subjects, ineligible = await load_subjects(
        db, run.scope_id, scale, only_risks=run.subject_ids
    )
    if not subjects and not ineligible:
        raise GenerationNotRunnable(
            "This project's register is empty, so there is nothing to evaluate. "
            "Identify risks first — a qualitative evaluation with no risks would be a "
            "model scoring things nobody has proposed."
        )

    skips: list[Skip] = list(ineligible)
    cap = max(config.generation_max_subjects, 0)
    for subject in subjects[cap:]:
        skips.append(
            Skip(
                subject.risk_code,
                SUBJECT_LIMIT,
                f"The pass stopped at its cap of {cap} risks. Run it again to continue.",
            )
        )
    subjects = subjects[:cap]

    run.provider = provider.name
    run.subject_ids = [s.risk_id for s in subjects]
    await db.commit()

    drops: list[Drop] = []
    transcript: list[dict] = []
    facts: dict[int, str | None] = {}
    calls = 0
    evidence_seen = 0
    offered = 0
    raised = 0
    input_tokens = 0
    output_tokens = 0
    model_name = ""

    for subject in subjects:
        items = await _evidence_for(
            db, subject, scope_id=run.scope_id, config=config, facts=facts
        )
        if not items:
            skips.append(
                Skip(
                    subject.risk_code,
                    NO_EVIDENCE,
                    "Nothing in the documents, the register or the schedule matched "
                    "enough of this risk to be worth citing, so no score was asked for.",
                )
            )
            continue
        evidence_seen += len(items)

        skip_areas = sorted(set(subject.scored_impacts) & scale.area_codes)
        content = agent.build_messages(
            subject,
            items,
            scale,
            project_name=project_name,
            skip_areas=skip_areas,
        )
        completion = await provider.complete(
            system=agent.SYSTEM_PROMPT,
            messages=[Message(role=USER, content=content)],
            max_tokens=config.llm_max_output_tokens,
            temperature=config.llm_temperature,
        )
        calls += 1
        model_name = completion.model or model_name
        input_tokens += completion.input_tokens or 0
        output_tokens += completion.output_tokens or 0

        assessment, subject_drops = agent.parse(
            completion.text,
            allowed_refs=frozenset(item.ref for item in items),
            scale=scale,
            skip_areas=skip_areas,
            skip_probability=subject.scored_probability is not None,
        )
        drops.extend(_labelled(subject, subject_drops))
        if assessment is not None or any(d.raw is not None for d in subject_drops):
            offered += 1

        made = 0
        if assessment is not None:
            made = await _propose(
                db,
                run,
                subject,
                assessment,
                items,
                model_name=model_name or run.model,
            )
            raised += made

        transcript.append(
            {
                "subject": subject.risk_code,
                "risk_id": subject.risk_id,
                "evidence_refs": [item.ref for item in items],
                "prompt_sha256": _sha(content),
                "response": completion.text[: config.generation_transcript_chars],
                "response_truncated_in_transcript": (
                    len(completion.text) > config.generation_transcript_chars
                ),
                "stop_reason": completion.stop_reason,
                "hit_output_ceiling": completion.truncated,
                "input_tokens": completion.input_tokens,
                "output_tokens": completion.output_tokens,
                "proposals": made,
                "dropped": [d.as_dict() for d in subject_drops],
            }
        )

    run.model = model_name or run.model
    run.chunk_count = evidence_seen
    run.window_count = calls
    run.candidate_count = offered
    run.proposal_count = raised
    run.pack_sha256 = _fingerprint(transcript)
    run.dropped = [d.as_dict() for d in drops] or None
    run.skipped = [s.as_dict() for s in skips] or None
    run.transcript = transcript or None
    run.input_tokens = input_tokens or None
    run.output_tokens = output_tokens or None
    await _finish(db, run, status=SUCCEEDED, error=None)


# --------------------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------------------


async def load_scale(db: AsyncSession) -> Scale:
    """The active matrix configuration, as the agent package's dataclasses.

    Read rather than written, because the platform already treats the scale as
    configuration: a client on a 4x4 with their own cost bands is a supported install.
    Level keys arrive from JSON as strings and are coerced here — the one place that
    conversion happens, rather than in the prompt renderer where a miss would silently
    print an empty descriptor.
    """
    config = await get_active_config(db)
    probability = tuple(
        Level(level=int(entry["level"]), label=str(entry.get("label", "")))
        for entry in config.get("probability_levels", [])
        if _is_level(entry)
    )
    impact = tuple(
        Level(level=int(entry["level"]), label=str(entry.get("label", "")))
        for entry in config.get("impact_levels", [])
        if _is_level(entry)
    )
    areas = tuple(
        ImpactArea(
            code=str(entry.get("code", "")).strip().upper(),
            name=str(entry.get("name", "")),
            descriptors={
                int(key): str(value)
                for key, value in (entry.get("descriptors") or {}).items()
                if str(key).lstrip("-").isdigit()
            },
        )
        for entry in config.get("impact_areas", [])
        if str(entry.get("code", "")).strip()
    )
    return Scale(probability=probability, impact=impact, areas=areas)


def _is_level(entry: object) -> bool:
    return isinstance(entry, dict) and str(entry.get("level", "")).lstrip("-").isdigit()


async def load_subjects(
    db: AsyncSession,
    scope_id: int,
    scale: Scale,
    *,
    only_risks: list[int] | None = None,
) -> tuple[list[RiskSubject], list[Skip]]:
    """Risks in scope with something left to score, and the ones with nothing.

    Returns both halves. A risk named explicitly and already fully assessed is not a silent
    no-op: it comes back as a skip with its reason, so a run over five named risks that
    produces two proposals can say what happened to the other three.

    Ordered by risk code so a capped run takes the same subjects every time. An arbitrary
    order would make "run it again to continue" mean "run it again and get a different
    forty".
    """
    stmt = select(Risk).where(Risk.scope_id == scope_id)
    if only_risks:
        stmt = stmt.where(Risk.id.in_(only_risks))
    risks = list(await db.scalars(stmt))
    risks.sort(key=lambda r: (r.risk_code or "", r.id))
    if not risks:
        return [], []

    labels = await _category_labels(db, {r.subcategory_id for r in risks})
    codes = scale.area_codes

    eligible: list[RiskSubject] = []
    skipped: list[Skip] = []
    for risk in risks:
        scored = {
            str(key).upper(): int(value)
            for key, value in (risk.impact_scores or {}).items()
            if isinstance(value, int) and not isinstance(value, bool)
        }
        unscored_areas = codes - set(scored)
        if risk.probability is not None and not unscored_areas:
            skipped.append(
                Skip(
                    risk.risk_code,
                    ALREADY_ASSESSED,
                    "A person has already scored the probability and every impact area. "
                    "Clear a field to ask for a second opinion on it.",
                )
            )
            continue
        eligible.append(
            RiskSubject(
                risk_id=risk.id,
                risk_code=risk.risk_code,
                title=risk.title,
                statement=_statement(risk),
                category=labels.get(risk.subcategory_id, "uncategorised"),
                scored_probability=risk.probability,
                scored_impacts=scored,
            )
        )
    return eligible, skipped


async def _category_labels(db: AsyncSession, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(RbsCategory, RbsSubcategory)
            .join(RbsSubcategory, RbsSubcategory.category_id == RbsCategory.id)
            .where(RbsSubcategory.id.in_(ids))
        )
    ).all()
    return {
        sub.id: f"{category.code}-{sub.code} {sub.name}" for category, sub in rows
    }


def _statement(risk: Risk) -> str:
    parts = [risk.description, risk.causes, risk.consequences]
    return " ".join(part.strip() for part in parts if part)


# --------------------------------------------------------------------------------------
# evidence
# --------------------------------------------------------------------------------------


async def _evidence_for(
    db: AsyncSession,
    subject: RiskSubject,
    *,
    scope_id: int,
    config: Settings,
    facts: dict[int, str | None],
) -> list[EvidenceItem]:
    """What this risk is allowed to be scored on, or nothing.

    ``scope_id`` is the project, and passing it does two things at once. Documents and
    schedule activities are narrowed to this project, which is right — another project's
    drawings are not evidence about this one. The register is *not* narrowed, because
    ``history_across_scopes`` overrides it there, and that is the point of the substrate:
    a project that has not run a workshop yet has an empty register, which is exactly when
    this generator is most useful. What the scope still buys on that side is the
    ``from_other_scope`` flag — without a scope to compare against, the evidence service
    cannot tell a sibling project's precedent from this project's own history, and an
    unlabelled precedent reads in an inbox as the latter.

    The subject's own register row is removed from the results. It would otherwise match
    itself perfectly on every term and take the top slot, and a suggestion citing the risk
    it is about as its own evidence is circular in a way that reads, in an inbox, exactly
    like a well-evidenced one.
    """
    found = await search(
        db,
        query=subject.query(),
        scope_id=scope_id,
        sources=SOURCES,
        limit=config.generation_evidence_limit + 1,
        history_across_scopes=True,
    )
    if found.abstained:
        return []

    self_ref = f"{HISTORY}:{subject.risk_id}"
    items: list[EvidenceItem] = []
    for hit in found.results:
        if hit.ref == self_ref:
            continue
        items.append(await _item(db, hit, facts))
        if len(items) >= config.generation_evidence_limit:
            break
    return items


async def _item(
    db: AsyncSession, hit: Evidence, facts: dict[int, str | None]
) -> EvidenceItem:
    """One retrieved thing, with a register hit's own scores attached.

    Those scores are the reference class and the reason history is searched at all. They
    are rendered as a sentence rather than as numbers because the model is being shown a
    precedent to weigh, and a bare integer in a prompt is copied far more readily than a
    line saying who scored it.
    """
    assessed = None
    if hit.kind == HISTORY:
        risk_id = _ref_id(hit.ref)
        if risk_id is not None:
            if risk_id not in facts:
                facts[risk_id] = await _assessed_label(db, risk_id)
            assessed = facts[risk_id]
    return EvidenceItem(
        kind=hit.kind,
        ref=hit.ref,
        excerpt=hit.excerpt,
        label=hit.source_label,
        from_other_scope=hit.from_other_scope,
        assessed=assessed,
    )


async def _assessed_label(db: AsyncSession, risk_id: int) -> str | None:
    risk = await db.get(Risk, risk_id)
    if risk is None:
        return None
    parts: list[str] = []
    if risk.probability is not None:
        parts.append(f"probability {risk.probability}")
    scores = risk.impact_scores or {}
    scored = ", ".join(
        f"{str(code).upper()} {value}"
        for code, value in sorted(scores.items())
        if isinstance(value, int) and not isinstance(value, bool)
    )
    if scored:
        parts.append(scored)
    return "; ".join(parts) or None


def _ref_id(ref: str) -> int | None:
    _, _, raw = ref.partition(":")
    try:
        return int(raw)
    except ValueError:
        return None


# --------------------------------------------------------------------------------------
# proposals
# --------------------------------------------------------------------------------------


async def _propose(
    db: AsyncSession,
    run: GenerationRun,
    subject: RiskSubject,
    assessment: Assessment,
    items: list[EvidenceItem],
    *,
    model_name: str,
) -> int:
    """Up to two rows: one for the probability, one for the impact scores.

    Separate because they are separately dispositionable, and because the ledger's
    one-pending-per-field index then supersedes each half on its own when the pass is run
    again. One combined row would make a reviewer who disagrees about the likelihood reject
    a set of impact judgements they had no quarrel with.
    """
    refs = _refs(assessment, items)
    raised = 0

    if assessment.probability is not None:
        await proposal_ledger.propose(
            db,
            scope_id=run.scope_id,
            target_type=TARGET_TYPE,
            target_id=subject.risk_id,
            field_path=PROBABILITY_FIELD,
            proposed_value=assessment.probability,
            observed_value=subject.scored_probability,
            rationale=_rationale(assessment.probability_rationale, items),
            evidence_refs=refs,
            confidence=assessment.probability_confidence,
            generator_model=model_name,
            generator_prompt_version=agent.PROMPT_VERSION,
            generation_run_id=run.id,
        )
        raised += 1

    if assessment.impacts:
        merged = dict(subject.scored_impacts)
        merged.update(assessment.impacts)
        await proposal_ledger.propose(
            db,
            scope_id=run.scope_id,
            target_type=TARGET_TYPE,
            target_id=subject.risk_id,
            field_path=IMPACTS_FIELD,
            proposed_value=merged,
            observed_value=subject.scored_impacts or None,
            rationale=_impacts_rationale(subject, assessment, items),
            evidence_refs=refs,
            confidence=assessment.impact_confidence,
            generator_model=model_name,
            generator_prompt_version=agent.PROMPT_VERSION,
            generation_run_id=run.id,
        )
        raised += 1

    return raised


def _refs(assessment: Assessment, items: list[EvidenceItem]) -> list[dict]:
    """``[{kind, ref, excerpt}]`` — the ledger's shape, and the evidence service's.

    A register hit's excerpt is prefixed with its label. ``Evidence.as_ref`` drops the
    label and the cross-scope flag, and an inbox row citing another project's risk without
    naming the project reads as this project's own history — which is the one thing it is
    not.
    """
    by_ref = {item.ref: item for item in items}
    out: list[dict] = []
    for ref in assessment.evidence_refs:
        item = by_ref.get(ref)
        if item is None:  # pragma: no cover - parse already filtered to the shown set
            continue
        excerpt = item.excerpt
        if item.kind == HISTORY:
            where = " (another project)" if item.from_other_scope else ""
            scored = f" Scored there as: {item.assessed}." if item.assessed else ""
            excerpt = f"{item.label}{where}: {excerpt}{scored}"
        out.append({"kind": item.kind, "ref": item.ref, "excerpt": excerpt})
    return out


def _rationale(text: str, items: list[EvidenceItem]) -> str:
    return "\n\n".join(part for part in (text.strip(), _basis(items)) if part)


def _impacts_rationale(
    subject: RiskSubject, assessment: Assessment, items: list[EvidenceItem]
) -> str:
    """Per-area reasoning, then what accepting this would and would not change.

    The carried-through line is not decoration. ``impact_scores`` is one JSON column that
    the applier sets whole, so the payload necessarily contains values a person judged and
    the model did not. Saying which is which on the face of the proposal is the difference
    between a merge and a silent claim of authorship.
    """
    lines = [
        f"{code}: {assessment.impacts[code]} — "
        f"{assessment.impact_rationales.get(code, 'no reason given')}"
        for code in sorted(assessment.impacts)
    ]
    carried = sorted(set(subject.scored_impacts) - set(assessment.impacts))
    if carried:
        lines.append("")
        lines.append(
            f"Already scored by a person and carried through unchanged: "
            f"{', '.join(carried)}. Accepting this changes only "
            f"{', '.join(sorted(assessment.impacts))}."
        )
    return "\n\n".join(part for part in ("\n".join(lines), _basis(items)) if part)


def _basis(items: list[EvidenceItem]) -> str:
    """What the judgement rests on, and what that evidence is not.

    The reference-class caveat is on every proposal rather than in a note somewhere,
    because the number in front of a reviewer is a probability and the thing behind it is
    another analyst's opinion. This platform holds no realised outcomes yet — that is
    S11 — and a suggestion that reads as though it does is the approximation this stage
    would be worst at hiding.
    """
    counts: dict[str, int] = {}
    for item in items:
        counts[item.kind] = counts.get(item.kind, 0) + 1
    parts = []
    if counts.get(DOC):
        parts.append(f"{counts[DOC]} document extract(s)")
    if counts.get(HISTORY):
        parts.append(f"{counts[HISTORY]} comparable risk(s) from the register")
    if counts.get(SCHEDULE):
        parts.append(f"{counts[SCHEDULE]} schedule activity(ies)")
    basis = "Basis: " + ", ".join(parts) + "." if parts else ""
    if counts.get(HISTORY):
        basis += (
            " Register comparables are other analysts' judgements, not observed "
            "outcomes — this platform holds no realised frequencies yet."
        )
    return basis


def _labelled(subject: RiskSubject, drops: list[Drop]) -> list[Drop]:
    """Every drop carries the risk it came from.

    A pass over forty subjects produces one flat list, and a drop reading "probability was
    7" without naming the risk is a number a reviewer cannot act on.
    """
    return [
        Drop(reason=d.reason, detail=f"{subject.risk_code}: {d.detail}", raw=d.raw)
        for d in drops
    ]


# --------------------------------------------------------------------------------------
# bookkeeping
# --------------------------------------------------------------------------------------


def _fingerprint(transcript: list[dict]) -> str:
    """Hash of the prompts actually sent, in order.

    Not a seed and not claimed as one, exactly as in ``risk_generate``. Two runs sharing
    this value asked the same questions of the same evidence, which makes their answers
    comparable; it does not make them equal.
    """
    return _sha("|".join(str(entry.get("prompt_sha256", "")) for entry in transcript))


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
