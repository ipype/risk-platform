# APPLY — simulation tab: currency, PDF/CDF, three-way sensitivity

Folder-swap. Unpack over the repo root, paths intact. Seventeen files: fourteen
whole-file replacements, three new, and one deletion the zip cannot make itself.

## Commit message

```
sim: currency symbols, PDF/CDF views, three-way sensitivity tornado

Adds RiskSensitivity.delay_variance_share so the schedule tornado reads as a
contribution rather than only a ranking, and bumps ENGINE_VERSION to 1.2.0 so a
run that measured no delay share can be told from one that predates the field.
No simulated number moves.

Frontend: money prints a currency symbol sourced once from config.CURRENCY
(VITE_CURRENCY, default "$"); SCurve is replaced by DistributionChart, which
draws the CDF, the density, or both on one axis with user-taggable percentiles;
the tornado gains cost / schedule / both-together views behind one switch.

Also adds the first frontend test runner - esbuild plus react-dom/server, no new
dependencies - and fixes a pre-existing SVG <title> in the tornado that browsers
rendered as raw markup.
```

## Apply

1. Unpack the zip over the repo root.
2. **The one deletion the zip cannot make:**
   ```
   git rm frontend/src/components/sim/SCurve.tsx
   ```
   `DistributionChart.tsx` replaces it. `SCurve` had exactly one caller
   (`SimulationView`), which now uses the new component. ROI's `CurveOverlay` is a
   different component and is untouched apart from its axis ticks.
3. `cd frontend && npm test && npm run build`
4. `cd backend && python -m pytest -q`

## Files

**Backend**
- `app/sim/sensitivity.py` — new `RiskSensitivity.delay_variance_share`
- `app/sim/engine.py` — computes it; `ENGINE_VERSION` 1.1.0 → 1.2.0
- `tests/sim/test_engine.py` — new `TestDelayVarianceShare`, four cases

**Frontend**
- `src/config.ts` — new `CURRENCY`
- `src/components/sim/format.ts` — currency symbol; new `fmtCompactMoney`, `fmtCompactUnits`
- `src/components/sim/DistributionChart.tsx` — **new**, replaces `SCurve.tsx`
- `src/components/sim/Tornado.tsx` — three metrics; `<title>` fix
- `src/components/sim/JointScatter.tsx` — axis ticks carry their units
- `src/components/roi/CurveOverlay.tsx` — same
- `src/simulation-types.ts` — `delay_variance_share`
- `src/views/SimulationView.tsx` — new chart, new `SensitivitySection`
- `src/simulation.css` — chart, chip-input and readout styles
- `test/run.mjs`, `test/sim-charts.test.tsx` — **new**
- `package.json`, `tsconfig.json` — `test` script, `test` in `include`

## Verified

Against a fresh `--depth 1` clone with the zip unpacked over it, using the repo's
pinned dependencies (`requirements.txt` + `requirements-dev.txt`, `npm ci`):

- `pytest -q` — **917 passed, 3 skipped**. Well above the ~876 recorded in `ACTIVE.md`;
  the doc has drifted again. `APPLY.md` on `main` also refers to a migration `0020`
  while `ACTIVE.md` still says 0018.
- `ruff check` / `ruff format --check` — clean on all four touched backend files. The
  repo-wide run reports 3 pre-existing F401s and 80 unformatted files, none of them mine;
  left alone.
- `npm test` — 29 checks, all pass.
- `tsc --noEmit --strict` — clean.
- `vite build` — clean, 107 modules.

## Design decisions — flagged, all revertible

**1. The currency symbol is a constant, not a literal.** `format.ts` used to print no
symbol at all, with a docstring explaining that the platform has no per-project currency
field and inventing one would be the screen making something up. That reasoning was right
about correctness and wrong about reading: a column of bare six-figure numbers beside a
column of days is ambiguous on the page in a way it never is in someone's head, and every
reviewer supplied the missing `$` mentally anyway. So it prints — but from
`config.CURRENCY`, one read, overridable with `VITE_CURRENCY`. The day a project carries
its own currency, that constant becomes the field's default and nothing else has to move.
**To revert:** set `CURRENCY = ""`.

Because `format.ts` is shared, this reaches the ROI and mitigation views too, which is
intended.

**2. `delay_variance_share` is not renormalised to sum to one.** It is
`cov(risk's sampled schedule impact, project delay) / var(delay)` — the same estimator as
the cost side aimed at a different target. The shares fall short of one because delay is a
maximum over network paths, not a sum of the risks driving it. The shortfall is the
schedule's own duration uncertainty, and it is printed on the face of the chart: "the
register explains 31% of the spread in the finish date". Normalising would have made the
bars tidier and would have credited a three-risk register for a date an uncertain baseline
mostly decided. Below 40% explained, the caption says outright that mitigating these risks
will not move the finish much.

**Consequence:** a stakeholder used to schedule tornados that sum to 100% will ask about
this. That is the intended conversation.

**3. Percentile markers are user-taggable rather than fixed.** P50/P80 open by default;
P10/P50/P80/P90/P95 are one click, and any percentile can be typed. Contract regimes
differ — P90 for a sanction case, P50 for an unbiased forecast, P95 where a lender is
involved — and a screen hard-coding the analyst's own convention makes everyone else do
arithmetic against a picture. Arbitrary percentiles are interpolated off the 101-point
`s_curve`, which is the percentile function on a regular grid, so P73.5 works even though
it was never in the run request.

**4. Tagged values are read off a strip below the chart, not off the plot.** Eight labels
on a 720-unit axis collide, and a label nudged clear of a collision points at the wrong
place. Only a short `P80` tick stays on the plot.

**5. Density bars are a histogram, not a density.** Dividing by bin width would put the
y-axis in the 1e-8 range on a cost chart. The bars show the share of iterations per bin
and the caption says so, with the bin count and width, because the heights depend on the
binning and that should not be something the reader has to discover.

**6. Three sensitivity views behind one switch, not three stacked charts.** They answer
the same question about different outcomes; side by side they get read as a ranking that
disagrees with itself. Switching in place makes the disagreement the point — the top risk
on the budget is routinely not the top risk on the date.

**7. A frontend test runner, and not Vitest.** The largest call here and the most
reversible. The repo has shipped a dozen deliveries verified only by `tsc --noEmit` and
`vite build`, which prove components compile and prove nothing about what they draw.
Vitest plus jsdom plus Testing Library is four devDependencies and a config file to run
assertions against strings — and this codebase's entire charting layer is hand-rolled SVG
whose output *is* a string. `react-dom/server` renders it and esbuild compiles the TSX;
both are already installed, esbuild as Vite's own bundler. Zero new packages, two files,
no config.

What it deliberately cannot do: fire events, run effects, assert on layout. It covers
first render of pure presentational components. The day something needs a click is the day
the Vitest argument becomes worth having, and this file should lose rather than grow a
synthetic event system.

`tsconfig.json` now includes `test`, so a stale fixture breaks `npm run build` rather than
rotting quietly. The bundle is emitted to `node_modules/.cache/` rather than the system
temp directory, because `react` and `react-dom` are left external and Node resolves an
external from where the bundle sits.

**To revert:** delete `frontend/test/`, drop the `test` script, remove `"test"` from
`include`.

It earned its place immediately: it caught a **pre-existing** bug in `Tornado.tsx`, where
each row's `<title>` was built from a child array. Browsers render only a single text node
in an SVG `<title>`, so that tooltip has been showing raw markup to anyone who hovered a
bar. `tsc` and `vite build` were both perfectly happy with it.

## Not done — deliberately

- **Report renderers still print bare numbers.** `services/report/` HTML and XLSX output
  is unchanged, so it is now inconsistent with the screen. Out of scope for "simulation
  tab"; wants doing next, and is small once `CURRENCY` has a backend counterpart.
- **`gantt-util.fmtMoney`** handles minor units on its own path and is untouched.
- **`risk_cost` and `schedule_driven_cost`** are returned by the engine and still charted
  nowhere. `DistributionChart` would render either as-is.
