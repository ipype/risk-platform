/**
 * What a mitigation package bought.
 *
 * The Mitigate screen deliberately refuses to answer this question — a residual register
 * is a declaration, not a result — and this is where it gets answered, by running the
 * baseline and the residual and subtracting. The screen's job is to make that subtraction
 * hard to misread.
 *
 * Three decisions shape the layout. There is **one** settings form, not two: a matched
 * pair is two runs that differ in exactly one field, and offering separate baseline and
 * treated settings would invite the drift the API refuses anyway. The **basis** panel is
 * always rendered, never behind a toggle, because every figure here rests on
 * approximations — a percentile standard error read off an S-curve, a package cost frozen
 * at pairing — and a caveat that has to be opened is a caveat that gets skipped. And a
 * **stale or noise-bound** comparison says so above the number rather than beneath it.
 */

import { useCallback, useEffect, useState } from "react";
import { CurveOverlay } from "../components/roi/CurveOverlay";
import { HeadlineCards } from "../components/roi/HeadlineCards";
import { CriticalityShiftTable, RiskMoverTable } from "../components/roi/MoverTables";
import { getPlans } from "../mitigation-api";
import { RoiApiError, getComparison, launchPair, listComparisons } from "../roi-api";
import { DEFAULT_PAIR } from "../roi-types";
import type { PairRequest, RoiDetail, RoiSummary } from "../roi-types";
import { getSimulationOptions } from "../sim-api";
import type { Plan } from "../mitigation-types";
import type { VersionOption } from "../simulation-types";
import { fmtMoney } from "../components/sim/format";
import "../roi.css";

const PERCENTILES = [50, 80, 90];

export default function RoiView() {
  const [plans, setPlans] = useState<Plan[]>([]);
  const [versions, setVersions] = useState<VersionOption[]>([]);
  const [rows, setRows] = useState<RoiSummary[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [detail, setDetail] = useState<RoiDetail | null>(null);

  const [planId, setPlanId] = useState<number | null>(null);
  const [form, setForm] = useState<PairRequest>({ ...DEFAULT_PAIR, schedule_version_id: null });

  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadRows = useCallback(async () => {
    const list = await listComparisons();
    setRows(list);
    setSelected((current) =>
      current !== null && list.some((r) => r.id === current) ? current : (list[0]?.id ?? null)
    );
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const [planList, options] = await Promise.all([getPlans(), getSimulationOptions()]);
        setPlans(planList);
        setPlanId((current) => current ?? (planList[0]?.id ?? null));
        setVersions(options.schedule_versions);
        await loadRows();
      } catch (e) {
        setError(e instanceof Error ? e.message : "Could not load comparisons");
      } finally {
        setLoading(false);
      }
    })();
  }, [loadRows]);

  const loadDetail = useCallback(async (id: number, percentile?: number) => {
    try {
      setDetail(await getComparison(id, percentile));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load this comparison");
    }
  }, []);

  useEffect(() => {
    if (selected === null) {
      setDetail(null);
      return;
    }
    void loadDetail(selected);
  }, [selected, loadDetail]);

  async function run() {
    if (planId === null) return;
    setBusy(true);
    setError(null);
    try {
      const created = await launchPair(planId, form);
      await loadRows();
      setSelected(created.id);
      setDetail(created);
    } catch (e) {
      setError(
        e instanceof RoiApiError
          ? e.message
          : e instanceof Error
            ? e.message
            : "The pair could not be started"
      );
    } finally {
      setBusy(false);
    }
  }

  function set<K extends keyof PairRequest>(key: K, value: PairRequest[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  if (loading) return <div className="roi-view roi-boot">Loading…</div>;

  const comparison = detail?.comparison ?? null;
  const units = comparison?.total_cost?.units ?? "currency";

  return (
    <div className="roi-view">
      <aside className="roi-rail">
        <header className="roi-rail-head">
          <h2 className="roi-rail-title">Measure a package</h2>
        </header>

        {plans.length === 0 ? (
          <p className="roi-empty">
            No mitigation packages in this project yet. Build one on the Mitigate screen,
            materialise it, then come back.
          </p>
        ) : (
          <div className="roi-form">
            <label>
              Package
              <select
                value={planId ?? ""}
                onChange={(e) => setPlanId(e.target.value === "" ? null : Number(e.target.value))}
              >
                {plans.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>

            <label>
              Schedule
              <select
                value={form.schedule_version_id ?? ""}
                onChange={(e) =>
                  set("schedule_version_id", e.target.value === "" ? null : Number(e.target.value))
                }
              >
                <option value="">Cost only</option>
                {versions.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.project_name}
                    {v.is_current ? " (current)" : ""} — {v.accepted_mappings} mapped
                  </option>
                ))}
              </select>
            </label>

            <div className="roi-formrow">
              <label>
                Iterations
                <input
                  type="number"
                  min={100}
                  step={1000}
                  value={form.iterations ?? DEFAULT_PAIR.iterations}
                  onChange={(e) => set("iterations", Number(e.target.value))}
                />
              </label>
              <label>
                Seed
                <input
                  type="number"
                  min={0}
                  value={form.seed ?? DEFAULT_PAIR.seed}
                  onChange={(e) => set("seed", Number(e.target.value))}
                />
              </label>
            </div>
            <p className="roi-note">
              One seed, used by both runs. Where the register is unchanged the two draw the
              same numbers, so the difference between them is the package rather than the
              sampler.
            </p>

            <div className="roi-formrow">
              <label>
                Base cost
                <input
                  type="number"
                  min={0}
                  value={form.base_cost ?? 0}
                  onChange={(e) => set("base_cost", Number(e.target.value))}
                />
              </label>
              <label>
                Burn rate / day
                <input
                  type="number"
                  min={0}
                  value={form.burn_rate_per_day ?? 0}
                  onChange={(e) => set("burn_rate_per_day", Number(e.target.value))}
                  disabled={form.schedule_version_id == null}
                  title={
                    form.schedule_version_id == null
                      ? "A burn rate prices schedule delay, and a cost-only run has none."
                      : undefined
                  }
                />
              </label>
            </div>

            <label>
              Quote at
              <select
                value={form.percentile ?? DEFAULT_PAIR.percentile}
                onChange={(e) => set("percentile", Number(e.target.value))}
              >
                {PERCENTILES.map((p) => (
                  <option key={p} value={p}>
                    P{p}
                  </option>
                ))}
              </select>
            </label>

            <button className="btn primary" disabled={busy || planId === null} onClick={() => void run()}>
              {busy ? "Running both…" : "Run baseline and treated"}
            </button>
          </div>
        )}

        <header className="roi-rail-head">
          <h2 className="roi-rail-title">Comparisons</h2>
        </header>
        {rows.length === 0 ? (
          <p className="roi-empty">Nothing measured yet.</p>
        ) : (
          <ul className="roi-list">
            {rows.map((r) => (
              <li key={r.id}>
                <button
                  className={r.id === selected ? "roi-listbtn active" : "roi-listbtn"}
                  onClick={() => setSelected(r.id)}
                >
                  <span className="roi-listname">{r.name || r.plan_name}</span>
                  <span className="roi-listmeta">
                    {r.plan_name} · P{r.percentile} · {r.status}
                    {r.stale ? " · stale" : ""}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </aside>

      <main className="roi-main">
        {error !== null ? <p className="roi-banner error">{error}</p> : null}

        {detail === null ? (
          <p className="roi-empty">Select a comparison, or run a package to make one.</p>
        ) : (
          <>
            <header className="roi-head">
              <div>
                <h2 className="roi-title">{detail.name || detail.plan_name}</h2>
                <p className="roi-sub">
                  {detail.plan_name} · runs {detail.before_run_id} and {detail.after_run_id} ·
                  measured by {detail.created_by}
                </p>
              </div>
              <label className="roi-inline">
                Read at
                <select
                  value={comparison?.percentile ?? detail.percentile}
                  onChange={(e) => void loadDetail(detail.id, Number(e.target.value))}
                >
                  {PERCENTILES.map((p) => (
                    <option key={p} value={p}>
                      P{p}
                    </option>
                  ))}
                </select>
              </label>
            </header>

            {detail.status === "pending" ? (
              <p className="roi-banner">
                One or both runs are still going. Reopen this comparison when they finish.
              </p>
            ) : null}
            {detail.status === "failed" ? (
              <p className="roi-banner error">
                A run in this pair failed, so there is nothing to compare.{" "}
                {detail.before?.error ?? detail.after?.error ?? ""}
              </p>
            ) : null}
            {detail.issues.length > 0 ? (
              <div className="roi-banner error">
                <strong>These two runs are not comparable.</strong>
                <ul>
                  {detail.issues.map((issue) => (
                    <li key={issue}>{issue}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            {detail.stale ? (
              <p className="roi-banner warn">
                The package has been re-materialised since this pair was run. These numbers
                still record what was run; they no longer describe the package as it stands.
              </p>
            ) : null}
            {detail.cost_moved ? (
              <p className="roi-banner warn">
                An action has been re-costed since this pair was made. Cost figures below are
                the snapshot from then ({fmtMoney(detail.plan_budget)}); the package now costs{" "}
                {fmtMoney(detail.current_plan_budget)}.
              </p>
            ) : null}

            {comparison !== null ? (
              <>
                {comparison.warnings.length > 0 ? (
                  <ul className="roi-warnings">
                    {comparison.warnings.map((w) => (
                      <li key={w}>{w}</li>
                    ))}
                  </ul>
                ) : null}

                <HeadlineCards comparison={comparison} />

                <section className="roi-section">
                  <h3>Cost distribution, before and after</h3>
                  <CurveOverlay
                    curve={comparison.curve}
                    percentile={comparison.percentile}
                    units={units}
                  />
                </section>

                <section className="roi-section">
                  <h3>Where the reduction came from</h3>
                  <p className="roi-sub">
                    {comparison.risk_count_before} risk(s) in the baseline,{" "}
                    {comparison.risk_count_after} after treatment, {comparison.retired_count}{" "}
                    retired outright.
                  </p>
                  <RiskMoverTable movers={comparison.risk_movers} />
                </section>

                <section className="roi-section">
                  <h3>Criticality shifts</h3>
                  <CriticalityShiftTable movers={comparison.criticality_movers} />
                </section>

                <section className="roi-section roi-basis">
                  <h3>What these numbers rest on</h3>
                  <ul>
                    {comparison.basis.map((line) => (
                      <li key={line}>{line}</li>
                    ))}
                  </ul>
                </section>
              </>
            ) : null}
          </>
        )}
      </main>
    </div>
  );
}
