# P4 — Risk Scoring tab: five modifications

Folder-swap. Unpack over the repo root, paths intact. Ten code files — nine whole-file
replacements plus `0020_...py`, which is new — and this note.

```
unzip -o risk-scoring-2026-08-07.zip -d /path/to/Risk-Platform
```

`APPLY.md` sits at the root of the zip. Delete it after unpacking, or unpack with
`-x APPLY.md`.

## Files

**New**

```
backend/alembic/versions/0020_quant_dimension_bounds_and_base.py
```

**Replaced**

```
backend/app/models/quant.py
backend/app/api/routes/quant.py
backend/app/services/quant_validation.py
backend/app/services/sim_assembly.py
frontend/src/App.tsx
frontend/src/quant.css
frontend/src/quant/draft.ts
frontend/src/quant/types.ts
frontend/src/views/QuantifyView.tsx
frontend/src/components/quant/QuantPanel.tsx
```

`frontend/src/components/quant/DimensionEditor.tsx` is deliberately untouched — its
`children` slot already renders between the shape picker and the three-point inputs, which
is where the relocated controls belong.

## After unpacking

```
cd backend && alembic upgrade head
pytest -q
ruff check .
cd ../frontend && npx tsc --noEmit && npm run build
```

The migration is additive and nullable throughout. `alembic downgrade 0019` is a clean
drop with nothing to reconstruct.

## The five modifications

1. **Tab renamed.** `App.tsx` nav caption and the rail heading in `QuantifyView.tsx` now
   read "Risk Scoring". The `View` union member, the view filename, the API prefix and the
   coverage endpoint all keep `quantify` — renaming a URL to follow a caption breaks
   bookmarks to gain nothing. Comments in both files say so, so the mismatch does not read
   as an oversight later.

2. **Base amount under "Percent of base."** New `cost_base_value` column, surfaced as an
   input that appears only when the basis is `pct_of_base` and is cleared on write
   otherwise. Blank is legal and falls back to the run's `base_cost`, with a warning;
   zero or negative is an error.

3. **"Bounds are" moved into each impact box.** Now a per-dimension override
   (`cost_bound_interpretation`, `sched_bound_interpretation`), not one control drawn
   twice. Hidden for `cumulative`, `discrete` and `none`, which define their own support.

4. **Default shape is "Not assessed"** on both dimensions. Save is disabled with a plain
   hint until one is chosen, rather than the form firing a validation error at an SME who
   has not typed anything yet.

5. **Post-mitigation lists the mitigation actions.** Read-only table above the form:
   action, owner, status, cost, days, with a committed total. Cancelled actions are shown
   struck through and excluded from the total. Unpriced actions are counted and the total
   is labelled a floor.

## Two things to read before you commit

**The `bound_interpretation` docstring reversal.** `quant_validation.py` used to argue the
interpretation should stay shared because it records how the session was run. That argument
is preserved — for the *default* — but the shared value made a legitimate pair impossible
to encode at all: `triangular` errors under a percentile interpretation and `trigen` errors
under an absolute one, so a contract-capped delay beside a P10/P90 cost is rejected today.
The module docstring is rewritten with that reasoning rather than deleted.

`NULL` on a dimension means inherit. No backfill, so every stored row keeps the exact
interpretation it was validated and simulated under, and no run recorded before today
changes its answer. The first save after applying this writes the resolved value explicitly,
which is a no-op on the numbers.

**`pct_of_base` was half-built.** `RiskInput.cost_base_reference` already existed in
`sim/inputs.py` and the engine already computed `magnitude * (ref / 100.0)` with a fallback
to `RunConfig.base_cost`. There was no column feeding it, so every percentage cost has been
priced against the whole-project base. Modification 2 closes that, and `sim_assembly` emits
a run note naming the fallback whenever it still fires.

## What was validated, and what was not

Validated here:

- `py_compile` on all five Python files.
- Eight behavioural groups against `quant_validation`, including a regression check that
  the previously-rejected `trigen` cost + `triangular` schedule pair is **still** rejected
  when no override is set. The escape hatch is the only difference.
- `0020` emitted as offline Postgres DDL, upgrade and downgrade, both asserted fragment by
  fragment. SQLite additive pass against a pre-0020 table proves an existing row comes
  through as `('p10_p90', NULL, NULL, NULL)`.
- `tsc --strict --noUnusedLocals --noUnusedParameters` clean on `quant/types.ts`,
  `quant/draft.ts`, `QuantPanel.tsx` and `QuantifyView.tsx`, checked against stubs carrying
  the real signatures of everything they import. `App.tsx` was not type-checked — it pulls
  in thirteen view modules and the only change in it is one caption string.

**Not** validated, because the sandbox cannot clone a private repo: `pytest -q`, `ruff`,
`vite build` against real `main`.

Expect fallout in exactly two places:

- Any test constructing `EstimateInput` or `DimensionInput` **positionally** past
  `confidence` / `rationale`. Both new fields are appended last specifically to avoid this,
  but it is unproven.
- Any test asserting an exact warning count on a `pct_of_base` estimate. The
  "no base amount, the run's base cost will be used" warning is new and will fire.

SQLite note: `op.create_check_constraint` cannot run on SQLite — the same limitation `0012`
already lives with. Constraint coverage on SQLite comes from the model's `__table_args__`
via `create_all`, inline at CREATE TABLE.
