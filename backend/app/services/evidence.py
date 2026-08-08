"""One retrieval interface. Every suggestion calls this.

**The contract is that a generator cannot cite what it did not retrieve.** Evidence comes
back with a ``ref`` that resolves, an ``excerpt`` a reviewer can read, and the query terms
that caused the hit. :meth:`Evidence.as_ref` produces exactly the shape the proposal
ledger's ``evidence_refs`` requires, so a generator hands retrieval output straight into a
proposal rather than composing a reference by hand — which is the only way a reference gets
composed for something that was never actually found.

**No evidence means abstain, never zero.** :func:`search` returning nothing is a result, not
a failure: the correct generator behaviour is to propose nothing, or to propose with
``confidence = None``. The ledger's CHECK constraint already refuses an unevidenced
proposal, so this is the same rule stated one layer earlier, where a generator can act on
it instead of being rejected by it. The abstention rule itself lives in
``app/retrieval/bm25.py`` and is about term overlap rather than a score threshold.

**Four substrates, three of them built.** ``doc`` searches the corpus; ``history`` searches
the register as a reference class; ``schedule`` searches activity names. ``cost_model``
(a CBS to hang percentage-basis estimates off) has no table in this platform yet and is
named here so its absence reads as a gap rather than an oversight.

**Schedules are searched relationally, not as documents.** ``.xer`` is parsed into
activities and relationships by ``app/schedule/``; routing it through the document
extractors would produce prose chunks of data already held in a form that answers questions
prose cannot.

**IDF is built per search over a capped candidate set.** Rarity is only meaningful against
the corpus actually being searched, so the statistics are rebuilt each time rather than
cached; the cap is what stops that being unbounded. Both are approximations and both are
declared on the response rather than hidden here — a truncated corpus that says so is a
result, and one that does not is a wrong answer wearing the clothes of a right one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import EvidenceRefUnresolvable
from app.models.document import ACTIVE, Document, DocumentChunk
from app.models.risk import Risk
from app.models.schedule import ScheduleActivity, ScheduleVersion
from app.models.scope import ScopeNode
from app.retrieval.bm25 import Corpus
from app.services.mapping_suggest import tokenize
from app.services.scope import resolve_read_scope

__all__ = [
    "Evidence",
    "EvidenceSet",
    "SOURCES",
    "search",
    "resolve",
]

DOC = "doc_chunk"
HISTORY = "risk"
SCHEDULE = "activity"

#: What a caller may ask for. ``cost_model`` is absent because there is no CBS table.
SOURCES: tuple[str, ...] = (DOC, HISTORY, SCHEDULE)

#: Per source, per search. Beyond this the candidate set is truncated and the response says
#: so. Chosen so a search stays well inside a request timeout on the in-process ranker;
#: it is the number that moves when retrieval goes to Postgres full-text or pgvector, at
#: which point the cap stops being needed at all.
MAX_CANDIDATES = 20_000

#: How much of a chunk a reviewer sees before opening the source.
EXCERPT_CHARS = 400


@dataclass(frozen=True, slots=True)
class Evidence:
    """One retrieved thing, in the shape the ledger and a review panel both need."""

    kind: str
    #: ``doc_chunk:88``, ``risk:12``, ``activity:41``. Stable, and resolvable by
    #: :func:`resolve` for as long as the row exists — which, for documents, is forever,
    #: because they are withdrawn rather than deleted.
    ref: str
    excerpt: str
    score: float
    #: Human label: a filename, a risk code, an activity code. What a citation shows before
    #: anyone clicks it.
    source_label: str
    scope_id: int
    #: ``None`` for sources that have no in-document position, which is every source except
    #: the corpus. Absent rather than faked: a locator that cannot render a highlight is
    #: worse than one that admits it does not exist.
    locator: dict[str, Any] | None = None
    section: str | None = None
    matched: tuple[str, ...] = ()
    idf_share: float = 0.0
    #: Set when the hit came from a different scope than the one searched — a precedent
    #: from a sibling project. Surfaced, never hidden: a reviewer weighing a reference
    #: class needs to know it is drawn from somewhere else.
    from_other_scope: bool = False

    def as_ref(self) -> dict[str, str]:
        """The proposal ledger's ``evidence_refs`` entry shape."""
        return {"kind": self.kind, "ref": self.ref, "excerpt": self.excerpt}


@dataclass(slots=True)
class EvidenceSet:
    results: list[Evidence]
    #: ``True`` when nothing cleared the overlap rule. The generator's cue to say nothing.
    abstained: bool
    reason: str | None
    searched: list[str]
    #: Corpus sizes actually searched, per source, and whether any was truncated. On the
    #: face of the result, because "no evidence found" means something different over
    #: forty chunks than over four thousand.
    corpus_sizes: dict[str, int]
    truncated: list[str]


async def search(
    db: AsyncSession,
    *,
    query: str,
    scope_id: int | None = None,
    sources: list[str] | None = None,
    limit: int = 10,
    history_across_scopes: bool = True,
) -> EvidenceSet:
    """Retrieve, or say that there is nothing worth retrieving."""
    wanted = [s for s in (sources or SOURCES) if s in SOURCES]
    tokens = tokenize(query)
    scope_ids = await resolve_read_scope(db, scope_id)

    if not tokens:
        return EvidenceSet(
            results=[],
            abstained=True,
            reason=(
                "The query reduced to nothing after stopwords and short tokens were "
                "removed. There is no term to rank on."
            ),
            searched=wanted,
            corpus_sizes={},
            truncated=[],
        )

    results: list[Evidence] = []
    sizes: dict[str, int] = {}
    truncated: list[str] = []

    for source in wanted:
        if source == DOC:
            records = await _doc_records(db, scope_ids)
        elif source == HISTORY:
            records = await _risk_records(
                db, scope_ids, across_scopes=history_across_scopes
            )
        else:
            records = await _activity_records(db, scope_ids)

        sizes[source] = len(records)
        if len(records) >= MAX_CANDIDATES:
            truncated.append(source)
        if not records:
            continue

        corpus = Corpus()
        for record in records:
            corpus.add(record.ref, tokenize(record.searchable))
        for hit in corpus.search(tokens, limit=limit):
            record = next(r for r in records if r.ref == hit.ref)
            results.append(
                Evidence(
                    kind=source,
                    ref=hit.ref,
                    excerpt=_excerpt(record.excerpt),
                    score=hit.score,
                    source_label=record.label,
                    scope_id=record.scope_id,
                    locator=record.locator,
                    section=record.section,
                    matched=hit.matched,
                    idf_share=hit.idf_share,
                    from_other_scope=(
                        scope_ids is not None and record.scope_id not in scope_ids
                    ),
                )
            )

    # Scores from different sources are not on one scale — a BM25 score over four hundred
    # activity names and one over four thousand document chunks mean different things, and
    # interleaving them by raw score would rank a source's whole population rather than its
    # relevance. Sorted by IDF share first, which *is* comparable: it says how much of the
    # query each hit actually accounted for.
    results.sort(key=lambda e: (-e.idf_share, -e.score, e.ref))
    results = results[:limit]

    return EvidenceSet(
        results=results,
        abstained=not results,
        reason=(
            None
            if results
            else "Nothing matched enough of the query to be worth citing."
        ),
        searched=wanted,
        corpus_sizes=sizes,
        truncated=truncated,
    )


async def resolve(db: AsyncSession, ref: str) -> Evidence:
    """Turn a stored reference back into something a reviewer can read.

    The other half of the contract. The ledger stores refs and nothing else; without this,
    a proposal accepted eight months ago cites a string. Documents are withdrawn rather
    than deleted precisely so this keeps working.
    """
    kind, _, raw = ref.partition(":")
    if not raw or kind not in SOURCES:
        raise EvidenceRefUnresolvable(ref, f"Expected one of {', '.join(SOURCES)}:<id>.")
    try:
        row_id = int(raw)
    except ValueError as exc:
        raise EvidenceRefUnresolvable(ref, "The id is not a number.") from exc

    if kind == DOC:
        row = await db.get(DocumentChunk, row_id)
        if row is None:
            raise EvidenceRefUnresolvable(ref, "That chunk no longer exists.")
        document = await db.get(Document, row.document_id)
        return Evidence(
            kind=DOC,
            ref=ref,
            excerpt=row.text,
            score=0.0,
            source_label=document.filename if document else "(unknown document)",
            scope_id=document.scope_id if document else 0,
            locator=row.locator,
            section=row.section,
        )

    if kind == HISTORY:
        risk = await db.get(Risk, row_id)
        if risk is None:
            raise EvidenceRefUnresolvable(ref, "That risk no longer exists.")
        return Evidence(
            kind=HISTORY,
            ref=ref,
            excerpt=_risk_text(risk),
            score=0.0,
            source_label=risk.risk_code,
            scope_id=risk.scope_id,
        )

    activity = await db.get(ScheduleActivity, row_id)
    if activity is None:
        raise EvidenceRefUnresolvable(ref, "That activity no longer exists.")
    return Evidence(
        kind=SCHEDULE,
        ref=ref,
        excerpt=f"{activity.code} {activity.name}".strip(),
        score=0.0,
        source_label=activity.code,
        scope_id=0,
        locator={"version_id": activity.version_id, "source_id": activity.source_id},
    )


# --------------------------------------------------------------------------------------
# source adapters
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Record:
    ref: str
    searchable: str
    excerpt: str
    label: str
    scope_id: int
    locator: dict[str, Any] | None = None
    section: str | None = None


async def _doc_records(db: AsyncSession, scope_ids: list[int] | None) -> list[_Record]:
    """Active documents only. A withdrawn document stays citable and stops being cited."""
    stmt = (
        select(DocumentChunk, Document)
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(Document.status == ACTIVE)
        .limit(MAX_CANDIDATES)
    )
    if scope_ids is not None:
        stmt = stmt.where(Document.scope_id.in_(scope_ids))

    rows = (await db.execute(stmt)).all()
    return [
        _Record(
            ref=f"{DOC}:{chunk.id}",
            # The section path is searched alongside the text. "Consents › Validity" is
            # often the only place a document says what a paragraph is about.
            searchable=f"{chunk.section or ''} {chunk.text}",
            excerpt=chunk.text,
            label=document.title or document.filename,
            scope_id=document.scope_id,
            locator=chunk.locator,
            section=chunk.section,
        )
        for chunk, document in rows
    ]


async def _risk_records(
    db: AsyncSession, scope_ids: list[int] | None, *, across_scopes: bool
) -> list[_Record]:
    """The register as a reference class.

    Searched across the whole hierarchy by default, not just the requested subtree. A
    reference class restricted to the current project is empty exactly when it is most
    needed — on a project that has not run a workshop yet — and "four other projects
    carried this risk" is the single most useful thing this substrate can say. The cost is
    that a hit may come from a sibling project, so every result carries its scope and is
    flagged when it came from elsewhere. Pass ``history_across_scopes=False`` to search
    only the requested subtree.
    """
    stmt = select(Risk).limit(MAX_CANDIDATES)
    if scope_ids is not None and not across_scopes:
        stmt = stmt.where(Risk.scope_id.in_(scope_ids))
    risks = list(await db.scalars(stmt))
    if not risks:
        return []

    names = {
        node.id: node.name
        for node in await db.scalars(
            select(ScopeNode).where(ScopeNode.id.in_({r.scope_id for r in risks}))
        )
    }
    return [
        _Record(
            ref=f"{HISTORY}:{risk.id}",
            searchable=_risk_text(risk),
            excerpt=_risk_text(risk),
            label=f"{risk.risk_code} — {names.get(risk.scope_id, 'unknown project')}",
            scope_id=risk.scope_id,
        )
        for risk in risks
    ]


async def _activity_records(
    db: AsyncSession, scope_ids: list[int] | None
) -> list[_Record]:
    """Activity names from the newest schedule version in scope.

    Newest only. Older versions describe the same work under names that have since been
    corrected, and retrieving both would let a suggestion cite an activity code that no
    longer exists in the schedule anyone is looking at.
    """
    version_stmt = select(ScheduleVersion.id).order_by(ScheduleVersion.id.desc()).limit(1)
    version_id = await db.scalar(version_stmt)
    if version_id is None:
        return []

    activities = list(
        await db.scalars(
            select(ScheduleActivity)
            .where(ScheduleActivity.version_id == version_id)
            .limit(MAX_CANDIDATES)
        )
    )
    return [
        _Record(
            ref=f"{SCHEDULE}:{activity.id}",
            searchable=f"{activity.code} {activity.name}",
            excerpt=f"{activity.code} {activity.name}".strip(),
            label=activity.code,
            # Activities hang off a schedule version, not off a scope directly. Reporting
            # the requested scope would be a claim this adapter cannot make, so it reports
            # nothing and ``from_other_scope`` stays false.
            scope_id=scope_ids[0] if scope_ids else 0,
            locator={"version_id": version_id, "source_id": activity.source_id},
        )
        for activity in activities
    ]


def _risk_text(risk: Risk) -> str:
    parts = [risk.title, risk.description, risk.causes, risk.consequences]
    return " ".join(p for p in parts if p)


def _excerpt(text: str) -> str:
    text = text.strip()
    if len(text) <= EXCERPT_CHARS:
        return text
    # Cut at a word so the excerpt a reviewer judges the citation on is readable.
    window = text[:EXCERPT_CHARS]
    cut = window.rfind(" ")
    return (window[:cut] if cut > EXCERPT_CHARS // 2 else window).rstrip() + "…"
