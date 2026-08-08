import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  SimApiError,
  cancelRun,
  getRun,
  getRuns,
  getSimulationOptions,
  previewRun,
  startRun,
} from "../sim-api";
import type {
  RiskSensitivity,
  RunDetail,
  RunPreview,
  RunRequest,
  RunSummary,
  SimulationOptions,
  VersionOption,
} from "../simulation-types";
import CriticalityTable from "../components/sim/CriticalityTable";
import JointScatter, { JointVerdict } from "../components/sim/JointScatter";
import DistributionChart from "../components/sim/DistributionChart";
import Tornado, { TornadoMetric } from "../components/sim/Tornado";
import { fmtDays, fmtDuration, fmtMoney, fmtPercent } from "../components/sim/format";
import "../simulation.css";

/**
 * Configure a run, start it, read the answer.
 *
 * The screen is built around one claim: the number worth looking at is the *integrated*
 * P80, and the most common way a QSRA goes wrong is quoting the additive one instead. So
 * the reconciliation between the two is not buried in a details pane — when the engine
 * reports a gap, it sits directly under the headline figure, in the same visual weight as
 * the number it corrects.
 *
 * The second claim is that a contingency computed over part of the register, without
 * saying so, is worse than no contingency. Excluded risks and assembly notes render above
 * the result, not below it.
 *
 * Preview runs on every configuration change and is where every refusal surfaces — the
 * DCMA gate, calendar-day impacts, mixed calendars, unsimulable estimates. Finding out
 * about those after a ten-minute run is how people stop using the preview and start
 * guessing.
 */

const POLL_MS = 2000;

const DEFAULTS: RunRequest = {
  name: "",
  scenario: "pre_mitigation",
  schedule_version_id: null,
  iterations: 10000,
  seed: 12345,
  sampling: "lhs",
  base_cost: 0,
  burn_rate_per_day: 0,
  gate_override: false,
  gate_override_reason: "",
};

function versionLabel(v: VersionOption): string {
  const gate = !v.gate.assessed
    ? "gate not run"
    : v.gate.passed
      ? "gate passed"
      : `gate failed (${v.gate.failed_count})`;
  return `${v.project_name} — ${v.activity_count} activities, ${v.accepted_mappings} mapped, ${gate}`;
}

export default function SimulationView() {
  const [options, setOptions] = useState<SimulationOptions | null>(null);
  const [form, setForm] = useState<RunRequest>(DEFAULTS);
  const [preview, setPreview] = useState<RunPreview | null>(null);
  const [previewError, setPreviewError] = useState<SimApiError | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selected, setSelected] = useState<RunDetail | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Guards a slow preview response from overwriting a newer one. The form changes faster
  // than the server answers, and without this the panel settles on whichever request
  // happened to land last rather than on the current configuration.
  const previewToken = useRef(0);

  const set = useCallback(<K extends keyof RunRequest>(key: K, value: RunRequest[K]) => {
    setForm((f) => ({ ...f, [key]: value }));
  }, []);

  useEffect(() => {
    let alive = true;
    Promise.all([getSimulationOptions(), getRuns()])
      .then(([opts, list]) => {
        if (!alive) return;
        setOptions(opts);
        setRuns(list);
      })
      .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, []);

  const payload = useMemo<RunRequest>(
    () => ({
      ...form,
      name: form.name?.trim() || "",
      gate_override_reason: form.gate_override ? form.gate_override_reason : null,
    }),
    [form]
  );

  const payloadKey = JSON.stringify(payload);

  useEffect(() => {
    if (!options) return;
    const token = ++previewToken.current;
    setPreviewing(true);
    const timer = window.setTimeout(() => {
      previewRun(payload)
        .then((p) => {
          if (token !== previewToken.current) return;
          setPreview(p);
          setPreviewError(null);
        })
        .catch((e: unknown) => {
          if (token !== previewToken.current) return;
          setPreview(null);
          setPreviewError(
            e instanceof SimApiError ? e : new SimApiError(0, "unknown", String(e))
          );
        })
        .finally(() => {
          if (token === previewToken.current) setPreviewing(false);
        });
    }, 300);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [payloadKey, options]);

  // A queued or running run is polled until it stops being one. The eager development
  // path returns terminal immediately, so this costs nothing there.
  useEffect(() => {
    if (!selected || (selected.status !== "queued" && selected.status !== "running")) {
      return;
    }
    const timer = window.setTimeout(() => {
      getRun(selected.id)
        .then((fresh) => {
          setSelected(fresh);
          setRuns((list) => list.map((r) => (r.id === fresh.id ? { ...r, ...fresh } : r)));
        })
        .catch(() => {
          /* transient: the next tick tries again */
        });
    }, POLL_MS);
    return () => window.clearTimeout(timer);
  }, [selected]);

  async function onStart() {
    setStarting(true);
    setError(null);
    try {
      const run = await startRun(payload);
      setSelected(run);
      setRuns(await getRuns());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  }

  async function onOpen(id: number) {
    try {
      setSelected(await getRun(id));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  // Only offered while a run is still queued — see the route docstring for why running
  // and terminal runs are out of scope for this button.
  async function onCancel(id: number) {
    try {
      const run = await cancelRun(id);
      setSelected((s) => (s?.id === id ? run : s));
      setRuns((list) => list.map((r) => (r.id === id ? { ...r, ...run } : r)));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  if (loading) return <p className="muted">Loading…</p>;

  const versions = options?.schedule_versions ?? [];
  const scenario = options?.scenarios.find((s) => s.value === form.scenario);
  const gateBlocked = previewError?.code === "schedule_gate_blocked";
  const runnable = preview != null && !previewing;

  return (
    <div className="sim-layout">
      <section className="sim-config" aria-label="Run configuration">
        <h2 className="sim-h">Run configuration</h2>
        {error && <div className="error">{error}</div>}

        <label className="sim-field">
          <span>Name</span>
          <input
            value={form.name ?? ""}
            placeholder="e.g. Sanction estimate, pre-mitigation"
            onChange={(e) => set("name", e.target.value)}
          />
        </label>

        <label className="sim-field">
          <span>Scenario</span>
          <select value={form.scenario} onChange={(e) => set("scenario", e.target.value)}>
            {options?.scenarios.map((s) => (
              <option key={s.value} value={s.value} disabled={s.estimate_count === 0}>
                {s.label} ({s.estimate_count} estimates)
              </option>
            ))}
          </select>
        </label>

        <label className="sim-field">
          <span>Schedule</span>
          <select
            value={form.schedule_version_id ?? ""}
            onChange={(e) =>
              set("schedule_version_id", e.target.value ? Number(e.target.value) : null)
            }
          >
            <option value="">Cost only — no schedule</option>
            {versions.map((v) => (
              <option key={v.id} value={v.id}>
                {versionLabel(v)}
              </option>
            ))}
          </select>
        </label>
        {versions.length === 0 && (
          <p className="sim-hint">
            No schedule has been imported, so only a cost contingency is available. Import
            a <code>.xer</code> on the Schedule tab to simulate delay.
          </p>
        )}

        <div className="sim-grid2">
          <label className="sim-field">
            <span>Base cost</span>
            <input
              type="number"
              min={0}
              value={form.base_cost ?? 0}
              onChange={(e) => set("base_cost", Number(e.target.value))}
            />
          </label>
          <label className="sim-field">
            <span>Burn rate / day</span>
            <input
              type="number"
              min={0}
              value={form.burn_rate_per_day ?? 0}
              disabled={form.schedule_version_id == null}
              onChange={(e) => set("burn_rate_per_day", Number(e.target.value))}
            />
          </label>
        </div>
        <p className="sim-hint">
          The burn rate prices delay — extended overheads, supervision, plant standing
          time. It is multiplied by the delay inside each iteration and never against a
          percentile.
        </p>

        <div className="sim-grid2">
          <label className="sim-field">
            <span>Iterations</span>
            <input
              type="number"
              min={100}
              max={1000000}
              step={1000}
              value={form.iterations ?? 10000}
              onChange={(e) => set("iterations", Number(e.target.value))}
            />
          </label>
          <label className="sim-field">
            <span>Seed</span>
            <input
              type="number"
              min={0}
              value={form.seed ?? 12345}
              onChange={(e) => set("seed", Number(e.target.value))}
            />
          </label>
        </div>

        <label className="sim-field">
          <span>Sampling</span>
          <select
            value={form.sampling}
            onChange={(e) => set("sampling", e.target.value as "lhs" | "mc")}
          >
            <option value="lhs">Latin hypercube</option>
            <option value="mc">Plain Monte Carlo</option>
          </select>
        </label>

        {gateBlocked && (
          <div className="sim-gate">
            <h3>Schedule quality gate</h3>
            <p>{previewError?.message}</p>
            {previewError!.blockingFailures.length > 0 && (
              <ul>
                {previewError!.blockingFailures.map((f) => (
                  <li key={String(f)}>{String(f)}</li>
                ))}
              </ul>
            )}
            <label className="sim-check">
              <input
                type="checkbox"
                checked={form.gate_override ?? false}
                onChange={(e) => set("gate_override", e.target.checked)}
              />
              <span>Simulate anyway, and record why</span>
            </label>
            {form.gate_override && (
              <textarea
                value={form.gate_override_reason ?? ""}
                placeholder="Why this schedule is fit to simulate despite the failures"
                onChange={(e) => set("gate_override_reason", e.target.value)}
              />
            )}
            <p className="sim-hint">
              The reason is stored on the run and travels with every number it produces.
            </p>
          </div>
        )}

        {previewError && !gateBlocked && (
          <div className="error">
            <strong>{previewError.message}</strong>
            {previewError.issues.length > 0 && (
              <ul className="sim-issues">
                {previewError.issues.map((i) => (
                  <li key={i}>{i}</li>
                ))}
              </ul>
            )}
          </div>
        )}

        {preview && (
          <div className="sim-preview">
            <h3>This run would simulate</h3>
            <dl>
              <div>
                <dt>Risks</dt>
                <dd>{preview.risk_count}</dd>
              </div>
              <div>
                <dt>Mapped to the schedule</dt>
                <dd>{preview.mapped_risk_count}</dd>
              </div>
              <div>
                <dt>Activities</dt>
                <dd>{preview.activity_count}</dd>
              </div>
            </dl>
            {preview.excluded.length > 0 && (
              <details className="sim-excluded" open>
                <summary>
                  {preview.excluded.length} risk
                  {preview.excluded.length === 1 ? "" : "s"} excluded
                </summary>
                <ul>
                  {preview.excluded.map((x) => (
                    <li key={x.risk_id}>
                      <strong>{x.risk_code}</strong> {x.title} — {x.reason}
                    </li>
                  ))}
                </ul>
              </details>
            )}
            {preview.notes.length > 0 && (
              <ul className="sim-notes">
                {preview.notes.map((n) => (
                  <li key={n}>{n}</li>
                ))}
              </ul>
            )}
            <p className="sim-fingerprint">Inputs {preview.inputs_sha256.slice(0, 12)}</p>
          </div>
        )}

        <button
          className="btn primary sim-run"
          disabled={!runnable || starting}
          onClick={onStart}
        >
          {starting ? "Starting…" : "Run simulation"}
        </button>
        {scenario?.estimate_count === 0 && (
          <p className="sim-hint">
            Nothing has been elicited for this scenario yet. Quantify some risks first.
          </p>
        )}

        <h2 className="sim-h">Runs</h2>
        {runs.length === 0 ? (
          <p className="muted">No runs yet.</p>
        ) : (
          <ul className="sim-runs">
            {runs.map((r) => (
              <li key={r.id} className="sim-run-row">
                <button
                  className={selected?.id === r.id ? "sim-run-item active" : "sim-run-item"}
                  onClick={() => onOpen(r.id)}
                >
                  <span className={`sim-status ${r.status}`}>{r.status}</span>
                  <span className="sim-run-name">{r.name || `Run ${r.id}`}</span>
                  <span className="sim-run-meta">
                    {new Date(r.created_at).toLocaleString()} · {r.risk_count} risks
                  </span>
                </button>
                {r.status === "queued" && (
                  <button
                    className="link danger sim-run-cancel"
                    title="Cancel this queued run"
                    onClick={(e) => {
                      e.stopPropagation();
                      onCancel(r.id);
                    }}
                  >
                    Cancel
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="sim-result" aria-label="Result">
        {!selected && <div className="empty">Select a run, or start one.</div>}
        {selected && <RunResult run={selected} onCancel={onCancel} />}
      </section>
    </div>
  );
}

function RunResult({
  run,
  onCancel,
}: {
  run: RunDetail;
  onCancel: (id: number) => void;
}) {
  if (run.status === "failed") {
    return (
      <>
        <RunHeader run={run} />
        <div className="error">{run.error ?? "The run failed without recording why."}</div>
      </>
    );
  }
  if (run.status === "cancelled") {
    return (
      <>
        <RunHeader run={run} />
        <div className="empty">
          Cancelled{run.cancelled_by ? ` by ${run.cancelled_by}` : ""}
          {run.cancelled_at ? ` · ${new Date(run.cancelled_at).toLocaleString()}` : ""},
          before a worker claimed it.
        </div>
      </>
    );
  }
  if (run.status !== "succeeded" || !run.result) {
    return (
      <>
        <RunHeader run={run} />
        <div className="empty">
          {run.status === "queued" ? (
            <>
              Queued — waiting for a worker.
              <button className="link danger sim-cancel-inline" onClick={() => onCancel(run.id)}>
                Cancel this run
              </button>
            </>
          ) : (
            "Running…"
          )}
        </div>
      </>
    );
  }

  const result = run.result;
  const contingency = result.contingency;
  const p80 = contingency.contingency.find((p) => p.p === 80);
  const gap = contingency.additive_error_at_p80;

  return (
    <>
      <RunHeader run={run} />

      {(run.excluded.length > 0 || run.assembly_notes.length > 0) && (
        <div className="sim-caveats">
          {run.excluded.length > 0 && (
            <p>
              <strong>
                {run.excluded.length} risk{run.excluded.length === 1 ? " was" : "s were"}{" "}
                excluded from this run.
              </strong>{" "}
              {run.excluded.map((x) => x.risk_code).join(", ")} — the contingency below
              does not cover {run.excluded.length === 1 ? "it" : "them"}.
            </p>
          )}
          {run.assembly_notes.map((n) => (
            <p key={n}>{n}</p>
          ))}
        </div>
      )}

      <div className="sim-headline">
        <div className="sim-headline-figure">
          <span className="sim-headline-label">P80 contingency</span>
          <span className="sim-headline-value">{fmtMoney(p80?.value)}</span>
          <span className="sim-headline-sub">
            on a base of {fmtMoney(contingency.base_cost)} — total{" "}
            {fmtMoney(contingency.integrated_p80_total ?? (p80 ? p80.value + contingency.base_cost : null))}
          </span>
        </div>
        <div className="sim-split">
          <span>
            Cost risk <strong>{fmtPercent(contingency.cost_variance_share)}</strong>
          </span>
          <span>
            Schedule risk <strong>{fmtPercent(contingency.schedule_variance_share)}</strong>
          </span>
        </div>
      </div>

      {gap != null && contingency.additive_p80_total != null && (
        <div className="sim-reconcile">
          <h3>Why this is not the number you would get by adding P80s</h3>
          <p>
            Adding the P80 cost to the burn rate times the P80 delay gives{" "}
            <strong>{fmtMoney(contingency.additive_p80_total)}</strong> against the correct{" "}
            <strong>{fmtMoney(contingency.integrated_p80_total)}</strong> — a difference of{" "}
            <strong>{fmtMoney(gap)}</strong>. That arithmetic assumes the cost tail and the
            schedule tail land in the same iteration, which is a claim of perfect
            correlation nobody made. The integrated figure is the one to quote.
          </p>
        </div>
      )}

      {result.warnings.length > 0 && (
        <div className="sim-warnings">
          <h3>Findings</h3>
          <ul>
            {result.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      <h3 className="sim-h">Total cost</h3>
      <DistributionChart
        series={result.total_cost}
        defaultMarkers={[50, 80, 95]}
        accent="cost"
        idPrefix={`cost-${run.id}`}
      />

      <h3 className="sim-h">Contingency by confidence</h3>
      <div className="sim-table-wrap">
        <table className="sim-table">
          <thead>
            <tr>
              <th scope="col">Confidence</th>
              <th scope="col" className="num">
                Contingency
              </th>
              <th scope="col" className="num">
                Total
              </th>
            </tr>
          </thead>
          <tbody>
            {contingency.contingency.map((p) => (
              <tr key={p.p} className={p.p === 80 ? "sim-row-key" : undefined}>
                <td>P{p.p}</td>
                <td className="num">{fmtMoney(p.value)}</td>
                <td className="num">{fmtMoney(p.value + contingency.base_cost)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {result.delay_days && (
        <>
          <h3 className="sim-h">Schedule delay</h3>
          <p className="sim-note">
            In <strong>elapsed days</strong>, not working days. A schedule spanning several
            calendars has no single working week, so durations are converted to elapsed
            time before the network is run and the delay comes back on that axis.
          </p>
          <p className="sim-note">
            Measured against this engine's own deterministic forward pass, which finishes
            on day{" "}
            {fmtDays(result.deterministic.baseline_finish_day)} — not against the dates in
            the imported schedule, which came out of P6 under constraints and progress
            overrides this pass does not model.
          </p>
          <DistributionChart
            series={result.delay_days}
            defaultMarkers={[50, 80]}
            accent="sched"
            idPrefix={`delay-${run.id}`}
          />
        </>
      )}

      {result.delay_days && (
        <>
          <h3 className="sim-h">Cost and date together</h3>
          {result.joint ? (
            <>
              <JointVerdict joint={result.joint} />
              <JointScatter joint={result.joint} />
            </>
          ) : result.joint === null ? (
            <p className="sim-note">
              This run is too short to place a joint quantile in — a frontier drawn from a
              couple of hundred iterations looks exactly like one drawn from ten thousand, so
              none is drawn. Raise the iteration count to read the cost and the date together.
            </p>
          ) : (
            <p className="sim-note">
              This run predates the joint view (engine {result.manifest.engine_version}), so
              it carries no cost-and-date pairing. Re-run it — same seed and inputs reproduce
              the same numbers — to read the two together.
            </p>
          )}
        </>
      )}

      <h3 className="sim-h">What drives the answer</h3>
      <SensitivitySection
        rows={result.risk_sensitivity}
        hasSchedule={result.delay_days != null}
      />

      <h3 className="sim-h">Activity criticality</h3>
      <CriticalityTable rows={result.activity_criticality} />

      <h3 className="sim-h">Reproducibility</h3>
      <dl className="sim-manifest">
        <div>
          <dt>Engine</dt>
          <dd>{result.manifest.engine_version}</dd>
        </div>
        <div>
          <dt>Seed</dt>
          <dd>{result.manifest.seed}</dd>
        </div>
        <div>
          <dt>Iterations</dt>
          <dd>{result.manifest.iterations.toLocaleString()}</dd>
        </div>
        <div>
          <dt>Sampling</dt>
          <dd>
            {result.manifest.sampling === "lhs" ? "Latin hypercube" : "Monte Carlo"}
            {result.manifest.centered_lhs ? " (centred)" : ""}
          </dd>
        </div>
        <div>
          <dt>Chunk</dt>
          <dd>{result.manifest.chunk_size.toLocaleString()}</dd>
        </div>
        <div>
          <dt>Inputs</dt>
          <dd className="sim-mono">{result.manifest.inputs_sha256.slice(0, 16)}</dd>
        </div>
      </dl>
      {result.correlation.repaired && (
        <p className="sim-note">
          The requested correlation matrix was not positive definite and was repaired, the
          largest coefficient moving by {result.correlation.repair_max_delta.toFixed(3)}.
          That is a finding about the driver tagging, not an implementation detail.
        </p>
      )}
    </>
  );
}

/**
 * The three sensitivity readings, one at a time.
 *
 * One chart with a switch rather than three stacked charts: they answer the same question
 * about different outcomes, and side by side they get read as a ranking that disagrees
 * with itself. Switching in place makes the disagreement the point — the top risk on the
 * budget is routinely not the top risk on the date, and seeing the order change under the
 * same twelve labels is the finding.
 *
 * Its own component because `RunResult` returns early four times before it gets here, and
 * a hook after an early return is not a hook.
 */
function SensitivitySection({
  rows,
  hasSchedule,
}: {
  rows: RiskSensitivity[];
  hasSchedule: boolean;
}) {
  const [metric, setMetric] = useState<TornadoMetric>("combined");
  const options: { value: TornadoMetric; label: string }[] = [
    { value: "cost", label: "Cost" },
    ...(hasSchedule ? [{ value: "schedule" as TornadoMetric, label: "Schedule" }] : []),
    { value: "combined", label: "Both together" },
  ];
  // A cost-only run has no schedule reading to offer and must not be left showing one.
  const active = !hasSchedule && metric === "schedule" ? "combined" : metric;

  return (
    <>
      <div className="sim-jcl-controls" role="group" aria-label="Sensitivity measure">
        <span className="sim-jcl-controls-label">Contribution to</span>
        {options.map((o) => (
          <button
            key={o.value}
            type="button"
            className={active === o.value ? "sim-chip active" : "sim-chip"}
            aria-pressed={active === o.value}
            onClick={() => setMetric(o.value)}
          >
            {o.label}
          </button>
        ))}
      </div>
      <Tornado rows={rows} metric={active} />
    </>
  );
}

function RunHeader({ run }: { run: RunDetail }) {
  return (
    <header className="sim-result-head">
      <div>
        <h2>{run.name || `Run ${run.id}`}</h2>
        <p className="sim-run-meta">
          {run.scenario.replace("_", " ")} · {run.risk_count} risks ·{" "}
          {run.mapped_risk_count} mapped · {run.activity_count} activities · by{" "}
          {run.created_by} · {new Date(run.created_at).toLocaleString()}
          {run.duration_ms != null && ` · ${fmtDuration(run.duration_ms)}`}
        </p>
      </div>
      <span className={`sim-status ${run.status}`}>{run.status}</span>
      {run.gate_override && (
        <p className="sim-override">
          DCMA gate overridden: {run.gate_override_reason}
        </p>
      )}
    </header>
  );
}
