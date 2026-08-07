"""How a risk gets its identifier.

The code is ``<program>-<project>-<sequence>``: the abbreviation of the program (or
portfolio) the project sits under, the abbreviation of the project itself, and a number
that starts at 0001 in every project.

**The RBS no longer appears in the code.** It used to be ``ENV-030-0007``, which reads well
with one project and stops reading at two, because the identifier says which taxonomy
branch a risk came from and nothing about which register it belongs to. Category is still
stored, filtered on, exported and shown — it just stops being what the identifier is for.
A useful consequence: because the code no longer encodes the taxonomy, a miscategorised
risk can be recategorised in place instead of being deleted and re-raised under a new
number.

**Sequence is per project.** Every project's register starts at 0001. A global sequence
would hand the second project 0007 as its first risk because another project got there
first, which is not a register anyone would sign.

**A number is never reissued.** ``max(seq)`` over the live register is not enough: delete
the highest-numbered risk and the next one created takes its number back, and now
``WTR-PLA-0007`` means one thing in the register and a different thing in the report that
went out last week. The allocator therefore takes the high-water mark of the live register
*and* of every code ever written to ``risk_history`` under this prefix, which is the only
record that outlives a deleted risk. History carries no ``scope_id`` — the code prefix is
what identifies the scope there, which is one more thing the prefix bought us.

Renaming a scope resets the mark, because the prefix changes and codes issued under the
old prefix cannot collide with codes issued under the new one.

**An explicit ``code`` on the scope node always wins.** It is used as written, uppercased,
with whitespace collapsed. Only a node with no code gets an abbreviation derived from its
name, and that fallback exists so a fresh install can raise a risk before anyone has been
made to name anything. Two code-less projects with similar names can derive the same
abbreviation: nothing breaks, because uniqueness is ``(scope_id, risk_code)`` and that is
per project, but the two will share a high-water mark and so leave gaps in each other's
numbering. Setting ``code`` on the scope node is the fix, and it is why the field exists.

**A project with no parent gets a two-part code**, ``SOLO-0001``. Inventing a program above
a standalone project just to fill a segment would be ceremony, and ``scope.py`` is explicit
that a lone project is the shape of every install on day one.

The allocator is read-then-write and therefore racy under concurrent creates. That is
deliberate rather than overlooked: the loser of a race violates ``uq_risk_scope_code`` and
its insert fails, which is the correct outcome and is enforced by the database rather than
by this function remembering to be careful.
"""

from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.history import RiskHistory
from app.models.risk import Risk
from app.models.scope import ScopeNode

__all__ = ["SEQ_WIDTH", "abbreviate", "format_code", "next_code", "prefix_for"]

#: Zero padding on the sequence. Four digits sorts correctly as text up to 9999 risks in
#: one project, which is well past the point where a register has other problems. Text
#: ordering is load-bearing twice over: it is how a rollup sorts into project blocks, and
#: it is what lets the high-water query below take ``max()`` of a string column and mean
#: it numerically.
SEQ_WIDTH = 4

#: Ceiling on an abbreviation derived from a name. An explicit ``code`` is never truncated
#: — if it was typed, it was meant.
DERIVED_MAX = 6

_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")

#: Codes issued under this scheme, and nothing else. Guards the history scan against a
#: pre-0019 code that happens to share a prefix, and against a trailing segment that is
#: not a sequence at all.
_CODE_TAIL = re.compile(r"-(\d{%d,})$" % SEQ_WIDTH)

#: ``LIKE`` wildcards, escaped so a scope code containing one cannot widen the scan. An
#: explicit code is taken verbatim, so ``%`` and ``_`` are both reachable.
_LIKE_ESCAPE = "\\"


def abbreviate(node: ScopeNode) -> str:
    """The short form of one scope node, for use in a risk code.

    An explicit ``code`` is honoured verbatim (uppercased, whitespace collapsed). Without
    one, a multi-word name becomes its initials and a single word becomes its first four
    characters: "North Shore Tunnel" is ``NST``, "Depot" is ``DEPO``.
    """
    if node.code and node.code.strip():
        return " ".join(node.code.split()).upper()

    words = _NON_ALNUM.sub(" ", node.name or "").split()
    if not words:
        return "SCOPE"
    if len(words) >= 2:
        return "".join(word[0] for word in words)[:DERIVED_MAX].upper()
    return words[0][:4].upper()


async def prefix_for(db: AsyncSession, project: ScopeNode) -> str:
    """Everything in a risk code before the sequence, without the trailing separator.

    The parent is read directly rather than walked: ``assert_placement`` guarantees a
    project's parent is a program or a portfolio and never another project, so the nearest
    ancestor is the only ancestor that can contribute a segment.
    """
    project_part = abbreviate(project)
    if project.parent_id is None:
        return project_part
    parent = await db.get(ScopeNode, project.parent_id)
    if parent is None:
        # A dangling parent_id is a data fault, not a reason to refuse a risk. Fall back
        # to the two-part code rather than raising inside a create.
        return project_part
    return f"{abbreviate(parent)}-{project_part}"


def format_code(prefix: str, seq: int) -> str:
    return f"{prefix}-{seq:0{SEQ_WIDTH}d}"


def _like_pattern(prefix: str) -> str:
    escaped = (
        prefix.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", f"{_LIKE_ESCAPE}%")
        .replace("_", f"{_LIKE_ESCAPE}_")
    )
    return f"{escaped}-%"


async def _highest_issued(db: AsyncSession, prefix: str) -> int:
    """The largest sequence ever written to the trail under this prefix.

    ``max()`` on the text column is exact because the sequence is zero-padded to a fixed
    width, so lexical and numeric order agree. The result is re-parsed rather than trusted:
    a pre-0019 code, or a scope code containing a hyphen, can satisfy the ``LIKE`` without
    being a code this scheme issued.
    """
    highest = await db.scalar(
        select(func.max(RiskHistory.risk_code)).where(
            RiskHistory.risk_code.like(_like_pattern(prefix), escape=_LIKE_ESCAPE)
        )
    )
    if not highest:
        return 0
    match = _CODE_TAIL.search(highest)
    return int(match.group(1)) if match else 0


async def next_code(db: AsyncSession, project: ScopeNode) -> tuple[int, str]:
    """The next ``(seq, risk_code)`` for a project's register."""
    prefix = await prefix_for(db, project)
    live = await db.scalar(
        select(func.max(Risk.seq)).where(Risk.scope_id == project.id)
    )
    seq = max(int(live or 0), await _highest_issued(db, prefix)) + 1
    return seq, format_code(prefix, seq)
