# APPLY — P5 5.1 Proposal Ledger + Provenance

## Commit message

```
feat: proposal ledger, the substrate every P5 generator writes through

One polymorphic table addressed by (target_type, target_id, field_path). Nothing
generated reaches a domain table except through a human disposition, which makes
invariant 4 an architectural property rather than a per-feature UI affordance.

Two constraints held by the database, not the application: at least one evidence
reference per proposal, and at most one pending proposal per target field. A
disposition is terminal — no delete route, no rewrite. Applying happens inside the
disposition transaction, so accepted-but-not-applied is unreachable.

risk_history gains a nullable provenance column. NULL reads as human, which is the
correct value for every row written before the ledger existed.

Migration 0021. One applier ships (risk), writing through the same snapshot/diff/
rescore path the PATCH route uses.
```

## Apply

Folder-swap. Unpack over the repo root, paths intact. `claude/plans/` does not exist
yet — the zip creates it.

```bash
unzip -o p5-5.1-proposal-ledger.zip -d /path/to/Risk-Platform
cd /path/to/Risk-Platform/backend
python -m pytest -q            # expect 979 passed, 3 skipped
python -m ruff check .
```

Then, against a real database:

```bash
make migrate                   # 0020 -> 0021
```

## Files

New:
- `backend/app/models/proposal.py`
- `backend/app/api/routes/proposals.py`
- `backend/app/services/proposal_ledger.py`
- `backend/app/services/proposal_apply.py`
- `backend/alembic/versions/0021_proposal_ledger.py`
- `backend/tests/test_proposals_api.py` (39 tests)
- `backend/tests/test_proposal_migration.py` (12 tests)
- `claude/plans/proposal-ledger.md`

Modified:
- `backend/app/models/history.py` — `provenance` column + `RiskHistoryRead` field
- `backend/app/core/errors.py` — `ProposalError` family (append only)
- `backend/app/api/errors.py` — three handlers + registration
- `backend/app/db/base.py` — register `Proposal`
- `backend/app/main.py` — mount the router

No frontend files touched.

## Design decisions — flagged

**Revertible.**

1. **Provenance on `risk_history`, not on `risk`.** "Who decided this" is a question about
   an event; a column on the domain row would be overwritten by the next edit and would
   answer only for the most recent one. Reverting means moving the column and accepting
   that only the latest write's origin survives.

2. **`observed_value` and the staleness guard.** Not in the pipeline design. A proposal
   records the value it was drafted against; accepting one whose target has since been
   edited by a human returns 409 with both values and requires `confirm_stale=true`.
   Reverting means dropping the column and letting the model's value win silently, which is
   the behaviour without it.

3. **`APPLIABLE_RISK_FIELDS` whitelist.** Narrower than `RiskUpdate`: `status`,
   `risk_level`, `impact` and `custom_fields` are not proposable. `risk_level` and `impact`
   are *derived* by the applier's scoring pass — a generator that could set them directly
   could put a band on the register its own probability and impact do not support.
   Widening it is one frozenset.

4. **Park is a flag, and unaudited.** A parked proposal is still `pending`. Park/unpark
   writes no event row. If who-parked-what turns out to matter, it needs a table.

5. **No inbox UI.** Deliberate — it should be designed against real generated rows, not
   synthetic ones. Same reason `provenance` is not surfaced in the history view yet: every
   value is NULL until a generator exists, so the column would render empty on every row.

**Not revertible without a data decision:** the partial unique index. It is what makes a
second generator pass refresh the inbox instead of doubling it. Dropping it means deciding
what to do with the duplicates that then accumulate.

## Verification run

Fresh `git clone --depth 1` of `main` at `f362022`, zip unpacked over it, pinned deps
installed from `requirements.txt` + `requirements-dev.txt`.

- `python -m pytest -q` — **979 passed, 3 skipped** (baseline 928 + 51 new)
- `ruff check` — clean on every new and modified file
- Migration 0021 executed against SQLite via direct `upgrade()` — the CHECK, the partial
  index, the defaults and the downgrade all exercised. `alembic upgrade head` is not
  usable here and never has been: 0001 issues an unconditional `CREATE EXTENSION`.
- Migration 0021 rendered offline for the `postgresql` dialect — asserts
  `parked BOOLEAN DEFAULT false NOT NULL` (an integer default would be rejected by
  Postgres), the CHECK, and the partial index predicate.
- ORM metadata compared to migration DDL object by object: same tables, same indexes, same
  columns, same partial-index predicate. `alembic autogenerate` has nothing to revert.

## Known gap

Nothing has executed 0021 under Postgres. The offline render proves the DDL compiles for
the dialect; the partial index and the `RESTRICT` self-FK on `superseded_by` are unverified
under the engine that actually enforces them. Same shape as the existing scope-delete
cascade gap in `BACKLOG.md` → add to the Postgres regression file when one covers scopes.
