/**
 * Where a mitigation package becomes something a simulation can read.
 *
 * The screen answers one question and refuses to answer a second. It says what this
 * package costs and what it claims to leave behind; it does not say what the package
 * buys, because that is the difference between a baseline run and a post-mitigation run
 * and no arrangement of per-risk factors can be multiplied out into it. Materialising
 * writes the residual register; Simulate reads it.
 *
 * Two things are kept in front of the analyst rather than tucked away. Untreated risks
 * appear in the table at full size, because that is exactly how they will be simulated.
 * And the package's cost sits beside the residual, never inside it — deterministic money
 * added to a percentile is the additive-percentile mistake wearing a different hat.
 */

import { useCallback, useEffect, useState } from "react";
import { PlanCostPanel } from "../components/mitigation/PlanCostPanel";
import { ResidualTable } from "../components/mitigation/ResidualTable";
import { TreatmentEditor } from "../components/mitigation/TreatmentEditor";
import { updateAction } from "../api";
import {
  MitigationApiError,
  clearTreatment,
  createPlan,
  deletePlan,
  getPlan,
  getPlans,
  getResidual,
  getScopeActions,
  getTreatments,
  materialize,
  setTreatment,
  updatePlan,
} from "../mitigation-api";
import { DEFAULT_TREATMENT } from "../mitigation-types";
import type {
  MaterializeResult,
  Plan,
  PlanDetail,
  ResidualPreview,
  ScopeAction,
  Treatment,
  TreatmentWrite,
} from "../mitigation-types";
import { fmtMoney } from "../components/sim/format";
import "../mitigation.css";

const STATUSES = ["draft", "proposed", "approved", "rejected", "superseded"];

export default function MitigateView() {
  const [plans, setPlans] = useState<Plan[]>([]);
  const [planId, setPlanId] = useState<number | null>(null);
  const [detail, setDetail] = useState<PlanDetail | null>(null);
  const [residual, setResidual] = useState<ResidualPreview | null>(null);
  const [treatments, setTreatments] = useState<Treatment[]>([]);
  const [actions, setActions] = useState<ScopeAction[]>([]);
  const [selected, setSelected] = useState<number | null>(null);

  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [confirmNeeded, setConfirmNeeded] = useState(false);
  const [result, setResult] = useState<MaterializeResult | null>(null);

  const loadPlans = useCallback(async () => {
    const rows = await getPlans();
    setPlans(rows);
    setPlanId((current) =>
      current !== null && rows.some((p) => p.id === current) ? current : (rows[0]?.id ?? null)
    );
  }, []);

  useEffect(() => {
    (async () => {
      try {
        await loadPlans();
      } catch (e) {
        setError(e instanceof Error ? e.message : "Could not load mitigation plans");
      } finally {
        setLoading(false);
      }
    })();
  }, [loadPlans]);

  const loadPlan = useCallback(async (id: number) => {
    const [d, r, t, a] = await Promise.all([
      getPlan(id),
      getResidual(id),
      getTreatments(id),
      getScopeActions(),
    ]);
    setDetail(d);
    setResidual(r);
    setTreatments(t);
    setActions(a);
  }, []);

  useEffect(() => {
    if (planId === null) {
      setDetail(null);
      setResidual(null);
      return;
    }
    setResult(null);
    setConfirmNeeded(false);
    loadPlan(planId).catch((e) =>
      setError(e instanceof Error ? e.message : "Could not load that plan")
    );
  }, [planId, loadPlan]);

  async function guarded(work: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function addPlan() {
    const name = newName.trim();
    if (!name) return;
    await guarded(async () => {
      const created = await createPlan({ name });
      setNewName("");
      await loadPlans();
      setPlanId(created.id);
    });
  }

  async function removePlan(id: number) {
    if (!confirm("Delete this plan? The residual estimates it wrote are left in place.")) return;
    await guarded(async () => {
      await deletePlan(id);
      setPlanId(null);
      await loadPlans();
    });
  }

  async function saveTreatment(riskId: number, payload: TreatmentWrite) {
    if (planId === null) return;
    await guarded(async () => {
      await setTreatment(planId, riskId, payload);
      await loadPlan(planId);
    });
  }

  async function removeTreatment(riskId: number) {
    if (planId === null) return;
    await guarded(async () => {
      await clearTreatment(planId, riskId);
      await loadPlan(planId);
    });
  }

  async function assign(action: ScopeAction, toPlan: number | null) {
    await guarded(async () => {
      await updateAction(action.risk_id, action.id, { plan_id: toPlan });
      if (planId !== null) await loadPlan(planId);
    });
  }

  async function price(action: ScopeAction, budget: number | null, schedDays: number | null) {
    await guarded(async () => {
      await updateAction(action.risk_id, action.id, {
        budget,
        sched_days: schedDays,
      });
      if (planId !== null) await loadPlan(planId);
    });
  }

  async function runMaterialize(confirmed: boolean) {
    if (planId === null) return;
    setBusy(true);
    setError(null);
    try {
      const outcome = await materialize(planId, confirmed);
      setResult(outcome);
      setConfirmNeeded(false);
      await loadPlan(planId);
    } catch (e) {
      if (e instanceof MitigationApiError && e.needsConfirmation) {
        setConfirmNeeded(true);
        setError(e.message);
      } else {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <div className="mit-view mit-boot">Loading…</div>;

  const chosen = plans.find((p) => p.id === planId) ?? null;
  const treatmentFor = (riskId: number): TreatmentWrite | null => {
    const row = treatments.find((t) => t.risk_id === riskId);
    if (!row) return null;
    const { id: _id, plan_id: _plan, risk_id: _risk, ...rest } = row;
    return { ...DEFAULT_TREATMENT, ...rest };
  };
  const selectedLine = residual?.lines.find((l) => l.risk_id === selected) ?? null;

  return (
    <div className="mit-view">
      <aside className="mit-rail">
        <header className="mit-rail-head">
          <h2 className="mit-rail-title">Mitigation plans</h2>
          <div className="mit-newplan">
            <input
              value={newName}
              placeholder="New package name"
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void addPlan();
              }}
            />
            <button
              type="button"
              className="btn small primary"
              disabled={busy || newName.trim() === ""}
              onClick={() => void addPlan()}
            >
              Add
            </button>
          </div>
        </header>
        {plans.length === 0 ? (
          <p className="mit-empty">
            No packages yet. A package groups the actions you want to price and simulate
            together.
          </p>
        ) : (
          <ul className="mit-planlist">
            {plans.map((p) => (
              <li key={p.id}>
                <button
                  type="button"
                  className={p.id === planId ? "mit-planbtn active" : "mit-planbtn"}
                  onClick={() => setPlanId(p.id)}
                >
                  <span className="mit-planname">{p.name}</span>
                  <span className="mit-planmeta">
                    {p.status}
                    {p.materialized_at !== null && " · materialised"}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </aside>

      <main className="mit-main">
        {error !== null && <p className="mit-banner error">{error}</p>}

        {chosen === null || detail === null ? (
          <p className="mit-empty">Select or create a package to start.</p>
        ) : (
          <>
            <header className="mit-head">
              <div>
                <h2 className="mit-title">{chosen.name}</h2>
                <p className="mit-sub">
                  {detail.treated_count} risk{detail.treated_count === 1 ? "" : "s"} treated ·
                  created by {detail.created_by}
                </p>
              </div>
              <label className="mit-inline">
                Status
                <select
                  value={detail.status}
                  disabled={busy}
                  onChange={(e) =>
                    void guarded(async () => {
                      await updatePlan(detail.id, { status: e.target.value });
                      await loadPlan(detail.id);
                      await loadPlans();
                    })
                  }
                >
                  {STATUSES.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                className="link danger"
                disabled={busy}
                onClick={() => void removePlan(detail.id)}
              >
                Delete package
              </button>
            </header>

            <PlanCostPanel
              cost={detail.cost}
              actions={actions}
              busy={busy}
              planId={detail.id}
              onAssign={(a, to) => void assign(a, to)}
              onPrice={(a, b, d) => void price(a, b, d)}
            />

            <section className="mit-residual">
              <div className="mit-residual-head">
                <h3>Residual register</h3>
                {residual !== null && (
                  <p className="mit-sub">
                    {residual.lines.length} risk{residual.lines.length === 1 ? "" : "s"} with a
                    baseline · {residual.treated} reduced · {residual.retired} retired ·{" "}
                    {residual.untreated} untreated and carried through at full size
                  </p>
                )}
              </div>

              {residual !== null && (
                <p className="mit-note">
                  Expected impact across the register moves from{" "}
                  <strong>{fmtMoney(residual.base_cost_ev_total)}</strong> to{" "}
                  <strong>{fmtMoney(residual.residual_cost_ev_total)}</strong>. A sum of means,
                  not a contingency — run both scenarios in Simulate for that.
                </p>
              )}

              <ResidualTable
                lines={residual?.lines ?? []}
                selectedRiskId={selected}
                onSelect={(id) => setSelected((current) => (current === id ? null : id))}
              />

              {selectedLine !== null && (
                <TreatmentEditor
                  riskCode={selectedLine.risk_code}
                  title={selectedLine.title}
                  value={treatmentFor(selectedLine.risk_id)}
                  busy={busy}
                  onSave={(payload) => void saveTreatment(selectedLine.risk_id, payload)}
                  onClear={() => void removeTreatment(selectedLine.risk_id)}
                  onClose={() => setSelected(null)}
                />
              )}
            </section>

            <section className="mit-materialise">
              <div className="mit-materialise-head">
                <h3>Materialise</h3>
                <p className="mit-note">
                  Writes the post-mitigation estimates a residual run reads. Locked
                  estimates are stepped over — a run has frozen them.
                </p>
              </div>

              {residual !== null && residual.matches_materialized && (
                <p className="mit-banner ok">
                  The residual register on file is the one this package wrote.
                </p>
              )}

              {confirmNeeded && (
                <div className="mit-confirm">
                  <p>
                    {residual?.edited_since.length ?? 0} residual estimate(s) changed after this
                    package last wrote them. Overwriting replaces that work.
                  </p>
                  <button
                    type="button"
                    className="btn danger"
                    disabled={busy}
                    onClick={() => void runMaterialize(true)}
                  >
                    Overwrite and materialise
                  </button>
                  <button
                    type="button"
                    className="link"
                    disabled={busy}
                    onClick={() => setConfirmNeeded(false)}
                  >
                    Cancel
                  </button>
                </div>
              )}

              {!confirmNeeded && (
                <button
                  type="button"
                  className="btn primary"
                  disabled={busy || (residual?.lines.length ?? 0) === 0}
                  onClick={() => void runMaterialize(false)}
                >
                  Materialise residual register
                </button>
              )}

              {result !== null && (
                <div className="mit-result">
                  <p>
                    <strong>{result.written}</strong> written · {result.unchanged} unchanged ·{" "}
                    {result.retired} retired
                  </p>
                  {result.skipped_locked.length > 0 && (
                    <p className="mit-note">
                      Stepped over, frozen by a run: {result.skipped_locked.join(", ")}
                    </p>
                  )}
                  {result.orphans.length > 0 && (
                    <p className="mit-note warn">
                      Residuals with no baseline behind them any more, still feeding a
                      post-mitigation run: {result.orphans.join(", ")}
                    </p>
                  )}
                  {result.issues.length > 0 && (
                    <ul className="mit-issues">
                      {result.issues.map((issue) => (
                        <li key={issue}>{issue}</li>
                      ))}
                    </ul>
                  )}
                  <p className="mit-note">
                    Run the post-mitigation scenario in Simulate to find out what this package
                    is worth.
                  </p>
                </div>
              )}
            </section>
          </>
        )}
      </main>
    </div>
  );
}
