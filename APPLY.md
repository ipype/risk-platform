# APPLY — P5 5.5 Qualitative Evaluation Generator

Folder-swap. Unpack over the repo root, paths intact. Nineteen files: fourteen backend
sources (nine changed, five new), four new test files, one plan doc.

**Delete this file before committing.** A previous delivery left one in the tree.

## What lands

New:
```
backend/app/agents/_parsing.py
backend/app/agents/qual_eval.py
backend/app/services/qual_generate.py
backend/app/services/generation_execute.py
backend/alembic/versions/0024_generation_subjects.py
backend/tests/test_qual_eval_agent.py
backend/tests/test_qual_generate.py
backend/tests/test_qual_generation_api.py
backend/tests/test_qual_generation_migration.py
claude/plans/qualitative-evaluation.md
```

Changed:
```
backend/app/agents/__init__.py          registers qual_eval
backend/app/agents/types.py             + Scale/RiskSubject/Assessment/Skip, 5 reasons
backend/app/agents/risk_id.py           private parse helpers now delegate to _parsing;
                                        CLOSING_LINE named. No behaviour change — its 100
                                        existing tests pass untouched.
backend/app/api/routes/generation.py    + POST /generation/qualitative-evaluation, ?kind=
backend/app/core/config.py              + generation_max_subjects, generation_evidence_limit
backend/app/llm/fake.py                 answers the object-shaped contract too
backend/app/models/generation.py        + QUALITATIVE_EVALUATION, KINDS, subject_ids,
                                        skipped, skipped_count
backend/app/services/generation_dispatch.py   dispatches by kind
backend/app/tasks/generation.py               dispatches by kind
```

## Migration

`0024` adds two nullable JSON columns to `generation_run` (`subject_ids`, `skipped`).
Nothing is rewritten, no index is touched, `proposal` is not touched. Existing 5.4 runs read
identically afterwards with both columns NULL. No backup needed.

```
cd backend && alembic upgrade head
```

## Commit message

```
5.5 qualitative evaluation generator

Score risks already on the register against the active matrix, grounded in
retrieved evidence, as proposals for human disposition. First query-shaped
generator and first caller of services/evidence.search.

- app/agents/qual_eval.py: pure prompt and response admission. Scale read from
  matrix_config and sent in full; off-scale levels refused rather than clamped;
  no overall impact, which stays models/matrix.overall_impact's rule.
- app/services/qual_generate.py: retrieval abstaining means no model call is
  made. Two proposals per risk (probability, impact_scores) so each half is
  separately dispositionable and separately superseded on a rerun.
- A field a person set is never re-scored, and the values they set are merged
  into the impact_scores payload so accepting cannot erase them.
- Register comparables are declared as other analysts' judgements, not observed
  frequencies, in the prompt and in every rationale.
- app/agents/_parsing.py: parse guards shared with risk_id rather than copied.
- app/services/generation_execute.py: dispatch by run kind.
- 0024: generation_run.subject_ids, generation_run.skipped.

1316 passed, 3 skipped (was 1239).
```

## Verify

```
cd backend
pip install -r requirements.txt -r requirements-dev.txt --break-system-packages
python -m pytest -q          # expect 1316 passed, 3 skipped
ruff check .                 # expect the 3 pre-existing F401s on main, nothing new
```

Frontend is untouched by this delivery; `tsc --noEmit` and `vite build` are unaffected.

## Verification already done

Full suite run in a fresh `--depth 1` clone of `main` with this zip unpacked over it, not
in a working tree. Migration `0024` executed against SQLite via
`Operations` + `MigrationContext` (the CLI has never worked against SQLite for this repo —
`0001` has an unconditional `CREATE EXTENSION`) and rendered offline for Postgres, upgrade
and downgrade both.

## Two things to know

- **`LLM_PROVIDER` is still empty by default and still refuses.** Set it to `fake` to try
  the route without spending anything; the fake reads the real prompt and answers from the
  scale and evidence identifiers it finds in it.
- **A one-chunk corpus cannot produce a retrieval hit.** BM25 gives a term present in every
  candidate an IDF of zero by design, so a project with a single document chunk will have
  every risk skipped for want of evidence. Correct behaviour, surprising the first time.
