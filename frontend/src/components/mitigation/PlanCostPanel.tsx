/**
 * What the package costs, and which actions are in it.
 *
 * Money and days are kept apart and neither is folded into a contingency figure. The
 * unpriced count is on the face of the panel rather than in a tooltip: an action with no
 * budget and no duration is a hole in the cost side of the ledger, and a rollup that
 * quietly treats it as zero is the cost-side twin of dropping a risk from a run.
 */

import { useState } from "react";
import type { PlanCost, ScopeAction } from "../../mitigation-types";
import { fmtDays, fmtMoney } from "../sim/format";

interface Props {
  cost: PlanCost;
  actions: ScopeAction[];
  busy: boolean;
  onAssign: (action: ScopeAction, planId: number | null) => void;
  onPrice: (action: ScopeAction, budget: number | null, schedDays: number | null) => void;
  planId: number;
}

function num(value: string): number | null {
  if (value.trim() === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function PlanCostPanel({ cost, actions, busy, onAssign, onPrice, planId }: Props) {
  const [showAll, setShowAll] = useState(false);
  const visible = showAll ? actions : actions.filter((a) => a.plan_id === planId);

  return (
    <section className="mit-cost">
      <div className="mit-chips">
        <div className="mit-chip">
          <span className="k">Package cost</span>
          <strong>{fmtMoney(cost.total_budget)}</strong>
        </div>
        <div className="mit-chip">
          <span className="k">Programme consumed</span>
          <strong>{fmtDays(cost.total_sched_days)}</strong>
        </div>
        <div className="mit-chip">
          <span className="k">Actions</span>
          <strong>{cost.action_count}</strong>
        </div>
        {cost.unpriced_count > 0 && (
          <div className="mit-chip warn">
            <span className="k">Unpriced</span>
            <strong>{cost.unpriced_count}</strong>
          </div>
        )}
        {cost.cancelled_count > 0 && (
          <div className="mit-chip muted">
            <span className="k">Cancelled</span>
            <strong>{cost.cancelled_count}</strong>
          </div>
        )}
      </div>

      <p className="mit-note">
        Deterministic and additive, unlike contingency. This figure belongs beside a
        simulated contingency, never inside it.
      </p>

      <div className="mit-actionhead">
        <h3>Actions in this package</h3>
        <label className="mit-check">
          <input
            type="checkbox"
            checked={showAll}
            onChange={(e) => setShowAll(e.target.checked)}
          />
          <span>Show every action in scope</span>
        </label>
      </div>

      {visible.length === 0 ? (
        <p className="mit-empty">
          {showAll
            ? "No mitigation actions have been written in this scope yet. They are added on the risk, in the register."
            : "Nothing assigned yet. Tick “show every action in scope” to pull actions into this package."}
        </p>
      ) : (
        <ul className="mit-actionlist">
          {visible.map((a) => (
            <li key={a.id} className={a.plan_id === planId ? "in" : ""}>
              <label className="mit-check">
                <input
                  type="checkbox"
                  disabled={busy}
                  checked={a.plan_id === planId}
                  onChange={(e) => onAssign(a, e.target.checked ? planId : null)}
                />
                <span className="mit-code">{a.risk_code}</span>
              </label>
              <span className="mit-actiontext">{a.action || "(untitled action)"}</span>
              <label className="mit-inline">
                Budget
                <input
                  type="number"
                  min="0"
                  disabled={busy}
                  defaultValue={a.budget ?? ""}
                  onBlur={(e) => {
                    const next = num(e.target.value);
                    if (next !== a.budget) onPrice(a, next, a.sched_days);
                  }}
                />
              </label>
              <label className="mit-inline">
                Days
                <input
                  type="number"
                  min="0"
                  disabled={busy}
                  defaultValue={a.sched_days ?? ""}
                  onBlur={(e) => {
                    const next = num(e.target.value);
                    if (next !== a.sched_days) onPrice(a, a.budget, next);
                  }}
                />
              </label>
              <span className="mit-status">{a.status}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
