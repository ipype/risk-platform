"""Ranking candidate activities for a risk, and validating a mapping once it exists.

Pure. No DB, no network, no logging. Rows in, ranked candidates out — the route layer
loads and converts. That keeps this file property-testable and keeps the scoring honest,
because nothing in here can quietly widen its own inputs.

Two things this module refuses to do, both on purpose:

**It never blends relevance with materiality.** "Is this the right activity for this risk"
and "does delay here move the finish date" are different questions. Blending them
produces a ranker that prefers the critical path, which is a good way to map every risk
onto the same twelve activities and understate everything else. They come back as
separate fields and the UI shows them on separate axes.

**A signal with no evidence abstains rather than scoring zero.** Precedent has nothing to
say on a fresh install; a category outside the lexicon has nothing to say ever. Scoring
those zero would drag every candidate down and make the confidence bands lie about how
much is known. An abstaining signal hands its weight to the others instead.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from app.services.mapping_lexicon import STOPWORDS, terms_for_category

#: Bumped when scoring changes in a way that alters output, so a stored
#: ``suggestion_score`` can be read against the code that produced it.
SUGGESTER_VERSION = "1.0.0"

# Relative weights. They do not need to sum to 1 — abstaining signals are removed and
# the remainder is renormalised, which is the whole reason these are relative.
WEIGHTS = {
    "lexical": 0.45,
    "taxonomy": 0.25,
    "wbs_affinity": 0.10,
    "precedent": 0.20,
}

STRONG, MODERATE, WEAK = 0.55, 0.30, 0.15

#: Short tokens that carry real meaning on a capital project and must survive the
#: minimum-length filter.
SHORT_KEEP = frozenset(
    {"ea", "po", "ifc", "ifr", "rfp", "rfq", "rfi", "qa", "qc", "hv", "lv", "ut", "ndt"}
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


# --------------------------------------------------------------------------- #
# tokenisation
# --------------------------------------------------------------------------- #


def _stem(token: str) -> str:
    """Crude suffix stripping. Enough to join install/installation, not a linguist.

    Only applied to longer tokens: shortening "piles" to "pile" is safe, shortening
    "gas" to "ga" is not.
    """
    if len(token) > 6:
        for suffix in ("ations", "ation", "ising", "izing", "ings", "ment"):
            if token.endswith(suffix) and len(token) - len(suffix) >= 4:
                return token[: -len(suffix)]
    if len(token) > 5:
        for suffix in ("ing", "ies", "ed"):
            if token.endswith(suffix) and len(token) - len(suffix) >= 4:
                return token[:-3] + "y" if suffix == "ies" else token[: -len(suffix)]
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize(text: str | None) -> list[str]:
    """Lowercase, split, drop stopwords and pure numbers, stem what is left."""
    if not text:
        return []
    out: list[str] = []
    for raw in _TOKEN_RE.findall(text.lower()):
        if raw in STOPWORDS:
            continue
        if raw.isdigit():
            continue
        if len(raw) < 3 and raw not in SHORT_KEEP:
            continue
        stemmed = _stem(raw)
        if stemmed and stemmed not in STOPWORDS:
            out.append(stemmed)
    return out


# --------------------------------------------------------------------------- #
# inputs
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ActivityRow:
    """Just enough of ``ScheduleActivity`` to rank and validate against."""

    source_id: str
    code: str
    name: str
    type: str
    status: str
    wbs_source_id: str | None = None
    wbs_path: str = ""
    original_duration_days: float | None = None
    remaining_duration_days: float | None = None
    total_float_days: float | None = None
    is_critical: bool = False
    constraint_type: str = "none"

    # -- shape predicates -------------------------------------------------- #
    # Matched on substrings so this works against normalised values ("milestone_start")
    # and against raw Primavera codes ("TT_Mile", "TK_Complete") alike. The parser's
    # exact vocabulary is not this module's business.

    @property
    def is_milestone(self) -> bool:
        return "mile" in self.type.lower()

    @property
    def is_level_of_effort(self) -> bool:
        t = self.type.lower()
        return "loe" in t or "effort" in t or "hammock" in t

    @property
    def is_summary(self) -> bool:
        t = self.type.lower()
        return "wbs" in t or "summary" in t

    @property
    def is_complete(self) -> bool:
        return "complete" in self.status.lower() or "finish" in self.status.lower()

    @property
    def is_in_progress(self) -> bool:
        s = self.status.lower()
        return "progress" in s or "active" in s or "started" in s

    @property
    def drivable(self) -> bool:
        """Can a multiplicative duration driver do anything to this activity at all."""
        return not (
            self.is_milestone or self.is_summary or self.is_level_of_effort or self.is_complete
        )


@dataclass(frozen=True)
class RiskRow:
    risk_id: int
    risk_code: str
    title: str
    description: str | None = None
    causes: str | None = None
    consequences: str | None = None
    category_code: str | None = None
    category_name: str | None = None
    subcategory_id: int | None = None
    subcategory_name: str | None = None

    @property
    def text(self) -> str:
        parts = [
            self.title,
            self.subcategory_name or "",
            self.causes or "",
            self.consequences or "",
            self.description or "",
        ]
        return " ".join(p for p in parts if p)


@dataclass
class Precedent:
    """Accept/reject counts per activity token, for one subcategory.

    Populated from ``MappingSuggestionOutcome``. Empty means the signal abstains.
    """

    accepts: Counter = field(default_factory=Counter)
    rejects: Counter = field(default_factory=Counter)

    @property
    def has_evidence(self) -> bool:
        return bool(self.accepts or self.rejects)

    def score(self, tokens: Sequence[str]) -> float | None:
        """Shrunk accept rate over the tokens that have been seen before.

        ``(a + 1) / (a + r + 2)`` keeps a single observation from reading as certainty.
        0.5 is neutral, so the return is stretched onto 0..1 with everything at or below
        neutral pinned to zero — precedent can promote a candidate, never invent one.
        """
        seen = [t for t in tokens if t in self.accepts or t in self.rejects]
        if not seen:
            return None
        rates = [(self.accepts[t] + 1) / (self.accepts[t] + self.rejects[t] + 2) for t in seen]
        mean = sum(rates) / len(rates)
        return max(0.0, min(1.0, (mean - 0.5) * 2))


# --------------------------------------------------------------------------- #
# outputs
# --------------------------------------------------------------------------- #


@dataclass
class Candidate:
    activity: ActivityRow
    score: float
    confidence: str
    signals: dict[str, float | None]
    matched_terms: list[str]
    recommended_type: str
    materiality: dict
    warnings: list[str]

    def as_dict(self) -> dict:
        a = self.activity
        return {
            "activity_source_id": a.source_id,
            "activity_code": a.code,
            "activity_name": a.name,
            "activity_type": a.type,
            "activity_status": a.status,
            "wbs_source_id": a.wbs_source_id,
            "wbs_path": a.wbs_path,
            "remaining_duration_days": a.remaining_duration_days,
            "total_float_days": a.total_float_days,
            "is_critical": a.is_critical,
            "score": round(self.score, 4),
            "confidence": self.confidence,
            "signals": {k: (None if v is None else round(v, 4)) for k, v in self.signals.items()},
            "matched_terms": self.matched_terms,
            "recommended_type": self.recommended_type,
            "materiality": self.materiality,
            "warnings": self.warnings,
        }


@dataclass
class ScopeSuggestion:
    """Offered when the top candidates cluster somewhere a filter could describe."""

    field: str
    op: str
    value: str
    label: str
    covered: int
    total_in_scope: int

    def as_dict(self) -> dict:
        return {
            "field": self.field,
            "op": self.op,
            "value": self.value,
            "label": self.label,
            "covered": self.covered,
            "total_in_scope": self.total_in_scope,
        }


# --------------------------------------------------------------------------- #
# corpus statistics
# --------------------------------------------------------------------------- #


class ActivityCorpus:
    """IDF over one version's activity names.

    Built once per version and reused across every risk in the session. Rarity is what
    makes "dewatering" outrank "concrete" on a project that is mostly concrete, and that
    only works if rarity is measured against *this* schedule.
    """

    def __init__(self, activities: Iterable[ActivityRow]) -> None:
        self.tokens_by_activity: dict[str, list[str]] = {}
        doc_freq: Counter = Counter()
        n = 0
        for act in activities:
            toks = tokenize(f"{act.name} {act.wbs_path}")
            self.tokens_by_activity[act.source_id] = toks
            doc_freq.update(set(toks))
            n += 1
        self.n_docs = max(n, 1)
        self._df = doc_freq

    def idf(self, token: str) -> float:
        # Smoothed so a token unseen in the schedule still scores above zero rather than
        # dividing by nothing.
        return math.log((self.n_docs + 1) / (self._df.get(token, 0) + 1)) + 1.0

    def tokens(self, source_id: str) -> list[str]:
        return self.tokens_by_activity.get(source_id, [])


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #

HARD_CONSTRAINTS = ("mand", "must", "mso", "meo", "start_on", "finish_on")


def validate_duration_driver(act: ActivityRow) -> list[str]:
    """Why this activity may not behave the way the analyst expects.

    Errors are prefixed ``error:``; everything else is advisory. The distinction matters
    at the API edge, where an error refuses the mapping and a warning is shown and
    recorded.
    """
    out: list[str] = []
    if act.is_milestone:
        out.append(
            "error: a milestone has no duration to stretch — model this as an "
            "inserted activity instead"
        )
    if act.is_summary:
        out.append(
            "error: summary duration is derived from its children, so driving it changes nothing"
        )
    if act.is_complete:
        out.append("error: activity is complete — there is no remaining duration to drive")
    if act.is_level_of_effort:
        out.append(
            "level of effort: duration follows its driving activities, so a driver here "
            "is absorbed rather than applied"
        )
    if act.is_in_progress and (act.remaining_duration_days or 0) <= 0:
        out.append("in progress with no remaining duration — the driver will do nothing")
    if any(c in (act.constraint_type or "").lower() for c in HARD_CONSTRAINTS):
        out.append(
            f"hard constraint ({act.constraint_type}) will absorb the delay or push the "
            "activity negative — the finish date may not move"
        )
    if act.total_float_days is not None and act.total_float_days > 60:
        out.append(
            f"{act.total_float_days:.0f} days of float — delay here is unlikely to reach "
            "the finish date"
        )
    return out


def validate_inserted_activity(
    predecessor: ActivityRow | None,
    successor: ActivityRow | None,
    linked: bool,
) -> list[str]:
    out: list[str] = []
    if predecessor is None:
        out.append("error: predecessor not found in this schedule version")
    if successor is None:
        out.append("error: successor not found in this schedule version")
    if predecessor is not None and successor is not None:
        if predecessor.source_id == successor.source_id:
            out.append("error: predecessor and successor are the same activity")
        elif not linked:
            out.append(
                "no existing relationship between these two — the inserted activity "
                "creates a new logic path rather than splitting one"
            )
        if successor.is_complete:
            out.append("successor is already complete — inserted work cannot delay it")
    return out


def validate_scope(matched: Sequence[ActivityRow]) -> list[str]:
    out: list[str] = []
    if not matched:
        out.append("error: this scope matches no activities in the current version")
        return out
    if len(matched) > 200:
        out.append(
            f"{len(matched)} activities matched — this is broad enough that one risk "
            "will drive most of the network"
        )
    complete = sum(1 for a in matched if a.is_complete)
    if complete:
        out.append(f"{complete} matched activities are complete and will be skipped")
    undrivable = sum(1 for a in matched if not a.drivable and not a.is_complete)
    if undrivable:
        out.append(
            f"{undrivable} matched activities are milestones, summaries or level of "
            "effort and cannot take a duration driver"
        )
    return out


def has_errors(warnings: Iterable[str]) -> bool:
    return any(w.startswith("error:") for w in warnings)


# --------------------------------------------------------------------------- #
# materiality — reported, never blended into relevance
# --------------------------------------------------------------------------- #


def materiality_of(act: ActivityRow) -> dict:
    float_days = act.total_float_days
    if act.is_critical or (float_days is not None and float_days <= 0):
        band = "high"
        why = "on the critical path — delay flows straight to the finish date"
    elif float_days is not None and float_days <= 20:
        band = "medium"
        why = f"near critical, {float_days:.0f} days of float"
    elif float_days is None:
        band = "unknown"
        why = "no float on this activity — schedule may not be fully calculated"
    else:
        band = "low"
        why = f"{float_days:.0f} days of float will absorb most delay"
    return {
        "band": band,
        "why": why,
        "total_float_days": float_days,
        "is_critical": act.is_critical,
        "remaining_duration_days": act.remaining_duration_days,
    }


# --------------------------------------------------------------------------- #
# scoring
# --------------------------------------------------------------------------- #


def _blend(signals: dict[str, float | None]) -> float:
    """Weighted mean over the signals that did not abstain.

    Renormalising rather than treating ``None`` as zero is the difference between "we
    have no precedent data" and "precedent says no".
    """
    live = {k: v for k, v in signals.items() if v is not None}
    if not live:
        return 0.0
    total_weight = sum(WEIGHTS[k] for k in live)
    if total_weight <= 0:
        return 0.0
    return sum(WEIGHTS[k] * v for k, v in live.items()) / total_weight


def _confidence(score: float) -> str:
    if score >= STRONG:
        return "strong"
    if score >= MODERATE:
        return "moderate"
    return "weak"


def suggest(
    risk: RiskRow,
    activities: Sequence[ActivityRow],
    corpus: ActivityCorpus,
    *,
    precedent: Precedent | None = None,
    already_mapped: frozenset[str] = frozenset(),
    accepted_wbs: frozenset[str] = frozenset(),
    limit: int = 15,
    min_score: float = WEAK,
) -> tuple[list[Candidate], ScopeSuggestion | None]:
    """Rank activities for one risk.

    ``already_mapped`` are source ids with a live mapping to this risk; they are dropped
    rather than shown greyed out, because a queue you have to re-skim is a queue you stop
    reading.
    """
    risk_tokens = tokenize(risk.text)
    if not risk_tokens:
        return [], None

    risk_token_set = set(risk_tokens)
    # Ceiling for the lexical signal: what a perfect match would score. Normalising
    # against this rather than against the best candidate keeps the number absolute —
    # a weak field stays weak instead of having its top row promoted to 1.0.
    ceiling = sum(corpus.idf(t) for t in risk_token_set) or 1.0

    cat_terms = terms_for_category(risk.category_code, risk.category_name)
    cat_stems = {_stem(t) for t in cat_terms}
    taxonomy_live = bool(cat_stems)

    prec = precedent if (precedent and precedent.has_evidence) else None

    candidates: list[Candidate] = []
    for act in activities:
        if act.source_id in already_mapped:
            continue
        if act.is_complete or act.is_summary:
            continue  # nothing a schedule risk can do to either

        act_tokens = corpus.tokens(act.source_id)
        if not act_tokens:
            continue
        act_token_set = set(act_tokens)

        shared = risk_token_set & act_token_set
        lexical = min(1.0, sum(corpus.idf(t) for t in shared) / ceiling)

        if taxonomy_live:
            cat_hits = act_token_set & cat_stems
            taxonomy: float | None = min(len(cat_hits), 2) / 2.0
        else:
            taxonomy = None
            cat_hits = set()

        if accepted_wbs:
            if act.wbs_source_id and act.wbs_source_id in accepted_wbs:
                wbs_affinity: float | None = 1.0
            else:
                wbs_affinity = 0.0
        else:
            wbs_affinity = None

        precedent_score = prec.score(act_tokens) if prec else None

        signals: dict[str, float | None] = {
            "lexical": lexical,
            "taxonomy": taxonomy,
            "wbs_affinity": wbs_affinity,
            "precedent": precedent_score,
        }
        score = _blend(signals)
        if score < min_score:
            continue

        warnings = validate_duration_driver(act)
        recommended = "inserted_activity" if act.is_milestone else "duration_driver"

        candidates.append(
            Candidate(
                activity=act,
                score=score,
                confidence=_confidence(score),
                signals=signals,
                matched_terms=sorted(shared | cat_hits),
                recommended_type=recommended,
                materiality=materiality_of(act),
                warnings=warnings,
            )
        )

    # Ties broken by materiality then by shorter name, so the specific activity wins over
    # the vague one that happens to contain the same word.
    band_rank = {"high": 0, "medium": 1, "unknown": 2, "low": 3}
    candidates.sort(
        key=lambda c: (
            -round(c.score, 4),
            band_rank.get(c.materiality["band"], 3),
            len(c.activity.name),
        )
    )
    top = candidates[:limit]
    return top, _scope_suggestion(top, activities)


def _scope_suggestion(
    top: Sequence[Candidate], activities: Sequence[ActivityRow]
) -> ScopeSuggestion | None:
    """Offer a filter when the good candidates all sit in one place.

    Twelve separate driver mappings onto one WBS branch is twelve rows to review, twelve
    rows to carry forward, and a gap the moment someone adds a thirteenth activity to
    that branch. One scoped driver is none of those things.
    """
    if len(top) < 4:
        return None
    considered = top[:15]
    counts = Counter(c.activity.wbs_source_id for c in considered if c.activity.wbs_source_id)
    if not counts:
        return None
    wbs_id, hits = counts.most_common(1)[0]
    if hits < 4 or hits / len(considered) < 0.6:
        return None

    in_scope = [a for a in activities if a.wbs_source_id == wbs_id and a.drivable]
    if len(in_scope) < 2:
        return None

    label = next(
        (c.activity.wbs_path for c in considered if c.activity.wbs_source_id == wbs_id),
        wbs_id,
    )
    return ScopeSuggestion(
        field="wbs",
        op="equals",
        value=wbs_id,
        label=label or wbs_id,
        covered=hits,
        total_in_scope=len(in_scope),
    )


# --------------------------------------------------------------------------- #
# scope resolution — shared by validation, coverage and (later) the sim engine
# --------------------------------------------------------------------------- #


def resolve_scope(scope: dict | None, activities: Sequence[ActivityRow]) -> list[ActivityRow]:
    """Activities a ``scoped_driver`` currently covers.

    Resolved on read rather than frozen at save time: a branch that gains activities on
    the next import should gain coverage, not silently keep the old list.
    """
    if not scope:
        return []
    field_name = str(scope.get("field", "")).lower()
    op = str(scope.get("op", "equals")).lower()
    value = str(scope.get("value", ""))
    if not field_name or not value:
        return []

    def attr(a: ActivityRow) -> str:
        if field_name == "wbs":
            return a.wbs_source_id or ""
        if field_name == "wbs_path":
            return a.wbs_path or ""
        if field_name == "activity_type":
            return a.type or ""
        if field_name == "name":
            return a.name or ""
        if field_name == "code":
            return a.code or ""
        return ""

    needle = value.lower()

    def match(a: ActivityRow) -> bool:
        got = attr(a).lower()
        if not got:
            return False
        if op == "equals":
            return got == needle
        if op == "starts_with":
            return got.startswith(needle)
        if op == "contains":
            return needle in got
        return False

    return [a for a in activities if match(a)]
