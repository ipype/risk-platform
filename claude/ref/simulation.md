# ref/simulation.md — the Monte Carlo engine

Open before editing `backend/app/sim/` or anything that assembles a run from the register,
the quantitative estimates or a parsed schedule.

Split out at creation rather than after the fact: these notes were already near the
~150-line threshold on day one, and the subsystem still has persistence, the Celery task
and the API surface to come. Cross-cutting invariants stay in `REFERENCE.md`; what is here
is specific to sampling, correlating, running the network and reporting the result.

Append, do not rewrite history.

## Invariants

### Purity

`app/sim/` has no database, no network, no logging, no clock. Seed in, arrays out. This is
not tidiness — invariant 6 says a run is reproducible, and the cheapest way to guarantee it
is to have nothing in the package that could vary. A timestamp in the manifest would be
enough to break it, which is why `RunManifest` carries none and recording *when* is the
persistence layer's job.

The dependency arrow points one way: `sim` may import `app.core.errors` (pure Python) and
nothing else from the app. It must never import `services`, which is why bound recovery and
scope resolution happen before a request is built rather than inside the engine.

### The uniforms are the substrate

Everything that shapes a run acts on one `(iterations, variables)` matrix of uniforms, and
the transform to magnitudes happens last. Latin hypercube stratification and Iman-Conover
reordering are both properties of the uniforms; a shape sampled by its own generator rather
than by inverse transform would silently opt out of both. Any new distribution must
therefore be added as a `ppf`, never as a `rvs`.

### One calendar

Durations reaching the engine are working days on `ScheduleInput.calendar_id`. Inside the
package a day is a float and there is nothing left to check it against, so this is the last
point at which the units invariant can be enforced at all.

## Gotchas

- **`np.percentile` interpolates linearly between order statistics.** At 10,000 centred
  strata the P10 of a uniform on `[0, 1M]` is 100,040 rather than 100,000, because the
  order statistic index is `(n-1) * p/100` and the values sit at `(k + 0.5) / n`. This is
  the convention every commercial tool reports and it is pinned in
  `test_a_uniform_cost_reproduces_its_analytic_percentiles`. Anyone reconciling a P80
  against a spreadsheet by hand will land here first.
- **Iman-Conover needs its accidental correlation stripped before imposing the target.**
  Two independently shuffled score columns correlate at roughly 0.03 at n = 1000, and
  skipping the `nearest_correlation(corr(scores))` decorrelation step rides that straight
  into the result. It is the classic implementation bug and it is invisible without a test
  that checks the *achieved* matrix.
- **Feeding a Spearman target straight to the Cholesky undershoots.** About 0.02 low at a
  target of 0.9, always in the direction of a thinner tail. `spearman_to_pearson` applies
  `2 sin(pi rho / 6)` first. The 1982 paper omits this; a contingency number cannot afford
  to.
- **A driver-tagged correlation matrix is very often not positive definite.** Three risks
  pairwise correlated at 0.8 through different drivers is not a statement about any joint
  distribution, and nothing in the tagging UI stops an analyst saying it. The repair is
  eigenvalue clipping, and `CorrelationReport.repair_max_delta` reports how far it moved —
  a repair of 0.4 is a finding about the tagging, not an implementation detail.
- **`str.replace` on `engine.py` fails silently when the target has drifted.** Cost about
  ten minutes on 2026-07-31: a call-site edit no-oped while the matching signature edit
  landed, producing an arity error two steps later. Read the region first, and check the
  replacement actually changed the file.
- **Column-major arrays are worth about a third of the CPM runtime.** Both passes read and
  write one activity's column across all iterations; in C order those elements sit a whole
  row apart. `forward` and `backward` allocate with `order="F"` for this reason and the
  chunk `dur` array must match.

## Decisions

### 2026-07-31 — the P3 engine, first cut

Delivered `app/sim/` (10 modules) and `tests/sim/` (7 files, 134 tests). Cost-only and
integrated cost-plus-schedule runs both work end to end. Persistence, the Celery task and
the API route are deliberately *not* in this delivery.

- **NumPy only. No SciPy, no Numba.** SciPy would have supplied `beta.ppf` and `norm.ppf`;
  `quant_validation.py` already rejected it once for the same reason and a wheel shipping
  its own BLAS is a large thing to add to an image for two functions. `special.py` owns
  Wichura AS241 for the normal quantile and a Lentz continued fraction plus bracketed
  Newton for the incomplete beta — both standard published numerics, both tested against
  closed forms rather than against another library. Numba became pointless once the CPM
  vectorised across iterations rather than across activities: the passes are `A + E`
  array operations regardless of iteration count. `numpy==2.5.1` is the only new pin.
- **Delay is measured against the engine's own deterministic forward pass**, not against
  the imported early finish. P6's dates came out under constraints, calendars and progress
  overrides this pass does not model, so subtracting them would report the difference
  between two CPM engines as risk. Both numbers are carried in `DeterministicView` so the
  gap stays visible.
- **Constraints are not modelled; `min_start_day` is offered instead.** Honouring a
  mandatory finish needs calendar arithmetic the package does not carry. The adapter
  converts a "start on or after" to working days from the data date outside the sim, and
  hard constraints are counted into a warning rather than silently ignored.
- **`duration_driver` adds the sampled delay to every driven activity, not divided among
  them.** This is what the mapping schema already committed to and why the API refuses
  `allocation_pct` on it. `inserted_activity` is the opposite and splits evenly when no
  allocation is given.
- **The tornado ranks on cost share plus apportioned schedule share.** The cost-side
  variance decomposition is exact — `sum_i cov(x_i, total) / var(total) == 1` for any sum —
  but delay is a maximum over network paths and has no exact additive split. The burn
  term's own share is exact and is divided among driving risks by covariance weight. This
  was a real bug found in the first end-to-end run: ranking on cost share alone sorted a
  risk with no direct cost, which owned 50% of the answer, to the bottom of its own
  tornado.
- **Chunk size is resolved from network size and written into the manifest.** Activity
  uniforms are drawn per chunk from a stream addressed by `(seed, chunk index)`, so
  replaying a run means replaying its chunking. Resolving it from the machine and not
  recording it would have been a silent reproducibility hole.
- **The engine reports the wrong answer next to the right one.** `ContingencyView` carries
  `additive_p80_total` alongside `integrated_p80_total`, and warns when the gap exceeds 1%
  *of the contingency* — not of the total, because a 120k error inside a 27m total reads as
  noise and inside a 3.6m contingency reads as 3%, and the contingency is the number the
  error corrupts. On the reference fixture the gap is about 3%.
- **A golden-value regression test pins the headline figure.**
  `test_the_headline_figure_is_pinned` fixes the P80 contingency, the mean delay and the
  inputs hash of a fixed request. Moving any of them means bumping `ENGINE_VERSION` and
  writing down why, per the `SYSTEM.md` standing rule.
