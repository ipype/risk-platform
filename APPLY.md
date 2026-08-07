# APPLY.md — register: scoped risk IDs, actions at creation, clickable rows

Folder-swap. Unpack over the repo root; paths are repo-relative. 15 files, 3 of them new.

```
backend/app/models/risk.py                                (rewritten)
backend/app/models/rbs.py                                 (rewritten)
backend/app/models/history.py                             (rewritten)
backend/app/api/routes/risks.py                           (rewritten)
backend/app/services/risk_code.py                         (NEW)
backend/alembic/versions/0019_risk_code_scope_prefix.py   (NEW)
backend/tests/test_risk_code_migration.py                 (NEW)
backend/tests/test_risk_codes_api.py                      (NEW)
frontend/src/types.ts                                     (rewritten)
frontend/src/columns.ts                                   (rewritten)
frontend/src/register.css                                 (NEW)
frontend/src/components/RiskTable.tsx                     (rewritten)
frontend/src/components/RiskFormPanel.tsx                 (rewritten)
frontend/src/components/MitigationActions.tsx             (rewritten)
frontend/src/views/RegisterView.tsx                       (rewritten)
```

## Steps

```bash
make migrate                                  # 0019. Renumbers every existing risk.
docker compose up -d --build api worker       # no new router, but the models changed
npm --prefix frontend install                 # no-op; no new deps
```

**Back up the database before `make migrate`.** 0019 is the first migration in this repo
that rewrites the value of a user-visible identifier on every existing row. The downgrade
is tested and reverses it, but a backup is cheaper than trusting that at the wrong moment.

## What changed

### 1. Risk ID is now `<program>-<project>-<sequence>`

`ENV-030-0007` becomes `WTR-PLA-0001`. The segments come from `ScopeNode.code`, falling
back to an abbreviation of `name` (initials for a multi-word name, first four characters
for a single word). A project with no parent gets two segments, `SOLO-0001` — inventing a
program above a standalone project would be ceremony, and `scope.py` is explicit that a
lone project is the day-one shape.

**Set `code` on your scope nodes.** Without one, two similarly-named projects derive the
same abbreviation. Nothing breaks — uniqueness is `(scope_id, risk_code)` and that is per
project — but the two registers read alike and share a high-water mark, so they leave gaps
in each other's numbering. This is the reason `ScopeNode.code` exists.

The RBS is out of the identifier. It is still stored, filtered, exported and shown; it
just is not what an identifier is for. Two consequences worth knowing:

- `RiskRead` now carries `subcategory_prefix`, `scope_id` and `seq`. Without the first,
  the register would have no way to display or edit a category at all.
- **Recategorisation is now possible** and I added it (`RiskUpdate.subcategory_prefix`,
  editable in the panel, audited as a `subcategory` change). Previously a miscategorised
  risk could only be fixed by deleting and re-raising it, because the code was built from
  the subcategory. This is the one thing here you did not ask for — say the word and I
  will pull it; it is ~15 lines across `risks.py` and `RiskFormPanel.tsx`.

**A number is never reissued.** `max(seq)` over the live register was not enough: delete
the highest-numbered risk and the next one takes its number back, so `WTR-PLA-0007` means
one thing in the register and another in last week's report. The allocator now also takes
the high-water mark from `risk_history`, which is the only record that outlives a deleted
risk. My first cut had this wrong and claimed otherwise in its docstring; the test
`test_a_deleted_risk_does_not_hand_its_number_to_the_next_one` is what caught it.

The old code had the same flaw. If you would rather not pay a second query per create,
delete `_highest_issued` and the `max(...)` in `next_code` — but then fix the docstring too.

### 2. Mitigation actions at risk creation

`RiskCreate.actions[]`, written in the same transaction, one `mitigation added` history
entry each — indistinguishable from an action added an hour later, because it is the same
event. Blank action text is refused with a 422 rather than silently written or silently
dropped; the panel disables Create and says which card is empty.

`MitigationActions` now takes `riskId: number | null`. With an id it behaves exactly as
before (fetch, save per card). With `null` it is controlled by the form above and nothing
is written until the risk is created. One component rather than two because the ninety
lines of fields are the whole component and they are identical in both modes.

### 3. Rows are clickable

Three affordances, deliberately: the row opens on click, the ID cell is a real `<button>`
so keyboard and screen-reader users are not left out, and the Edit link stays because the
ID column is hideable. Everything in the actions column stops propagation, or Delete opens
the panel behind its own confirm dialog.

Styles are in a new `frontend/src/register.css`, imported by `RegisterView`, rather than
appended to the 15KB shared `index.css`. Colours are alpha over whatever is behind them,
so they cannot drift from the palette.

`columns.ts` gains a hidden-by-default **Category** column, since the taxonomy is no longer
readable off the ID.

## Migration detail

0019 does three things:

1. Widens `risk.risk_code` **and `risk_history.risk_code`** from 20 to 100. The second one
   is the one that bites: miss it and the first edit after deploy 500s on a history insert,
   not on the migration.
2. Drops `uq_risk_scope_subcategory_seq`. It sequenced per subcategory, which is
   meaningless now. **Nothing replaces it** — `seq` feeds `risk_code` and nothing else, so
   a duplicate sequence in a scope is already refused by `uq_risk_scope_code`. This also
   means no existing test fixture can break on a new constraint.
3. Renumbers every risk, per scope, oldest first. Not optional: two risks in one project
   under different subcategories can both hold `seq = 1` today, which the new scheme cannot
   represent.

The rewrite runs in two passes (park every code at a temporary value, then write the real
one) because `uq_risk_scope_code` is live throughout and a one-pass rewrite can transiently
collide.

`risk_history` values are **not** rewritten. The trail records what the code was; a
migration that corrected it to agree with the present would be the one thing an append-only
trail exists to prevent. Column widens, rows keep what they were written with.

Abbreviation rules are inlined in the migration rather than imported from
`services/risk_code.py` — a migration pinned to live application code silently
re-interprets history the next time that code changes.

`alembic upgrade --sql` renders the DDL and emits a comment saying the data pass was
skipped, because renumbering needs a connection to read `scope_node`.

## Verification I ran

- `tests/test_risk_code_migration.py` — **16 passed**. Real `upgrade()` against a
  pre-0019 SQLite database with six risks across all four hierarchy shapes (project under
  program, project with no scope code, project under a portfolio with no program, project
  with no parent). Covers the renumber, the untouched history, the dropped constraint, the
  surviving one, the downgrade, and the offline Postgres render.
- `tests/test_risk_codes_api.py` — **21 passed**, against the real `risks` and
  `mitigations` routers on SQLite (harness copied from `test_scoped_reads.py`).
- `tsc --noEmit --strict` with `noUnusedLocals`/`noUnusedParameters` on all five changed
  frontend files against stubbed imports — clean. It caught one dead import.
- `py_compile` on every changed Python file.

**What I could not run:** the full `pytest -q` suite and `vite build`, because I have no
clone of the private repo. Your verification sequence covers both. The stub caveat: the
API tests ran against my reconstruction of `app/models/mitigation.py`, `app/api/errors.py`,
`app/core/errors.py` and `app/db/session.py`, built from the column set that
`mitigations.py` already constructs. Everything else was the real file.

## Things to check on your side

- **`git grep -n "risk_code" backend/app frontend/src`** — I audited `export.py` (clean:
  Category and Subcategory come from the RBS join, never from slicing the code) and fixed
  the one parser I found, `fromRisk` in `RiskFormPanel`, which sliced the first two
  segments to recover the subcategory. I did not open `services/report/` or `mappings.py`.
  Both look like display-only consumers from their call sites, but I have not read them.
- `export.py` sets the "Risk Code" column width to 16. Fine for `WTR-PLA-0001`; a verbose
  explicit scope code will display truncated. One-character fix if it bothers you — I did
  not deliver a 21KB file for a column width.
- Frontend test-runner gap, now several deliveries deep: these five files are verified by
  `tsc` and your `vite build`, not by a test runner. Still unresolved.
