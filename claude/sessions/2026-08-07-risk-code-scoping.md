# 2026-08-07 — risk code scoping, nested mitigation actions, clickable register rows

## Requested

Three register changes:
1. Risk ID → `<program abbreviation>-<project abbreviation>-<sequential number>`
2. Add mitigation actions at risk creation, not only after save-and-reopen
3. Clickable rows to edit a risk

## What shipped (delivered as `register-scoped-ids.zip`, not yet applied by Sam)

**1. Risk code.** `ENV-030-0007` → `WTR-PLA-0001`. New `app/services/risk_code.py`:
segments from `ScopeNode.code`, falling back to a name-derived abbreviation; a project
with no parent gets two segments rather than an invented program. Sequence is per
project via a high-water mark taken from both the live register and `risk_history` (see
below — the first cut of this was wrong). RBS moved out of the identifier entirely: it's
still stored, filtered, exported; `RiskRead` now carries `subcategory_prefix` explicitly,
and — a consequence I decided rather than asked about — recategorisation is now possible
via `RiskUpdate.subcategory_prefix`, audited as a `subcategory` change. Flagged explicitly
in `APPLY.md` as the one thing not literally requested.

Migration `0019_risk_code_scope_prefix.py`: widens `risk.risk_code` **and
`risk_history.risk_code`** 20→100 (the second one is the easy miss — a narrow history
column 500s on the first edit after deploy, not on the migration itself), drops
`uq_risk_scope_subcategory_seq` without replacement (`seq` feeds nothing else;
`uq_risk_scope_code` already covers the real constraint), and renumbers every existing
risk in a two-pass rewrite (temp value, then real value — the constraint is live
throughout). History *values* are untouched; only the column widens.

**2. Nested actions.** `RiskCreate.actions: list[NestedActionCreate]`, written in the
create transaction, one `mitigation added` history entry per action, blank text refused
(422) rather than silently written or dropped. `MitigationActions.tsx` now takes
`riskId: number | null` and runs in a draft mode when null — cards live in the parent
form's state and post with the risk in one request, rather than the risk being created
silently on first keystroke.

**3. Clickable rows.** Row `onClick`, plus the ID cell as a real `<button>` (keyboard/
screen-reader reachable), plus the existing Edit link kept since the ID column is
hideable. New `frontend/src/register.css` rather than growing the shared 15KB
`index.css`.

## A bug I introduced and caught before delivery

First cut of the sequence allocator used `max(seq)` over the *live* register only.
Deleting the highest-numbered risk in a project handed its number straight back to the
next create — exactly the thing the docstring claimed couldn't happen. Caught by my own
test (`test_a_deleted_risk_does_not_hand_its_number_to_the_next_one`), not by inspection.
Fixed by also taking the high-water mark from `risk_history`, the only record that
outlives a deleted risk — which the scope-prefixed code made queryable by prefix, since
history carries no `scope_id`. Costs one extra query per create. Documented in `APPLY.md`
as removable if Sam doesn't want to pay for it, with the note that the old (pre-existing)
code had the identical flaw and nobody had noticed.

## Verification actually run

No repo clone available this session (network egress doesn't include `github.com`'s raw
content beyond the MCP, and I did not attempt `git clone` — worth noting since a prior
session established that pattern as the standard). Verification instead:

- Migration: real `upgrade()`/`downgrade()` executed against a hand-built pre-0019 SQLite
  database covering all four hierarchy shapes (project/program, project/no-code,
  project/portfolio-no-program, project/no-parent) plus a duplicate-seq-across-subcategory
  fixture proving the renumber is not optional. 16/16 passed. Offline Postgres SQL render
  checked too.
- API: reconstructed `mitigation.py`, `api/errors.py`, `core/errors.py`, `db/session.py`
  as stubs from the column sets `mitigations.py` already implied, wired the real
  `risks.py` + models + service against them on SQLite. 21/21 passed.
- `tsc --noEmit --strict` with `noUnusedLocals`/`noUnusedParameters` on all five frontend
  files against stubbed API/history-util imports. Caught one dead import.
- `py_compile` on every Python file delivered.

**Not run**: the real `pytest -q` suite, `ruff`, `vite build`, or the fresh-clone
unpack-and-rerun step the standing verification method calls for — no clone existed to run
them against. This delivery has *not* been verified the way the standing method requires,
only against reconstructions. Said plainly in `APPLY.md` rather than left implicit.

## Audit done, and not done

Checked `export.py` (21KB) for other `risk_code` consumers: clean, Category/Subcategory
columns come from the RBS join, never from parsing the code. Did not open
`services/report/` or `mappings.py` — flagged as open in `APPLY.md`, not closed here.

## Decisions

- Recategorisation added unprompted (see above) — offered as revertible, not yet
  confirmed or reverted by Sam.
- High-water-mark allocator over risk_history rather than a dedicated sequence table or
  counter — reuses an existing append-only source rather than adding new state.

## Docs found stale at bootstrap (this session, not fixed by it)

`ACTIVE.md` still claimed 4.4 pending apply and 4.5/4.6 not started; both were long since
on `main` per `REFERENCE.md`'s own 2026-08-01/08-02/08-06 entries. Fifth occurrence of the
pattern `REFERENCE.md` already tracks as "no longer an incident, a standing condition."
The mechanical check proposed twice in `BACKLOG.md` still doesn't exist. Not attempted
this session either — this session had no clone to check a test count against, which is
itself the reason that check keeps not getting built: it needs the thing this session also
didn't have.
