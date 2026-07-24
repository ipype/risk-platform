import { useEffect, useState } from "react";
import type { MitigationAction } from "../types";
import { createAction, deleteAction, getActions, updateAction } from "../api";

const STATUSES = ["Proposed", "In progress", "Complete", "Cancelled"];
const EFFECTIVENESS = ["", "Low", "Medium", "High"];

interface Props {
  riskId: number;
}

export default function MitigationActions({ riskId }: Props) {
  const [actions, setActions] = useState<MitigationAction[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getActions(riskId)
      .then(setActions)
      .catch((e) => setError(String(e)));
  }, [riskId]);

  function field<K extends keyof MitigationAction>(
    id: number,
    key: K,
    value: MitigationAction[K]
  ) {
    setActions((list) =>
      list.map((a) => (a.id === id ? { ...a, [key]: value } : a))
    );
  }

  async function add() {
    setBusy(true);
    setError(null);
    try {
      const created = await createAction(riskId, { action: "" });
      setActions((list) => [...list, created]);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function save(a: MitigationAction) {
    setBusy(true);
    setError(null);
    try {
      const saved = await updateAction(riskId, a.id, {
        action: a.action,
        owner: a.owner || null,
        due_date: a.due_date || null,
        budget: a.budget,
        completion_pct: a.completion_pct,
        effectiveness: a.effectiveness || null,
        status: a.status,
      });
      setActions((list) => list.map((x) => (x.id === a.id ? saved : x)));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: number) {
    if (!confirm("Delete this mitigation action?")) return;
    setBusy(true);
    setError(null);
    try {
      await deleteAction(riskId, id);
      setActions((list) => list.filter((x) => x.id !== id));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mit-actions">
      <div className="mit-head">
        <span>Mitigation actions ({actions.length})</span>
        <button type="button" className="btn small" disabled={busy} onClick={add}>
          + Add action
        </button>
      </div>
      {error && <div className="error">{error}</div>}
      {actions.length === 0 && <p className="muted">No actions yet.</p>}

      {actions.map((a) => (
        <div className="action-card" key={a.id}>
          <textarea
            className="action-text"
            placeholder="Describe the mitigation action…"
            value={a.action}
            onChange={(e) => field(a.id, "action", e.target.value)}
          />
          <div className="action-fields">
            <label>
              Owner
              <input value={a.owner ?? ""} onChange={(e) => field(a.id, "owner", e.target.value)} />
            </label>
            <label>
              Due date
              <input
                type="date"
                value={a.due_date ?? ""}
                onChange={(e) => field(a.id, "due_date", e.target.value || null)}
              />
            </label>
            <label>
              Budget
              <input
                type="number"
                value={a.budget ?? ""}
                onChange={(e) =>
                  field(a.id, "budget", e.target.value === "" ? null : Number(e.target.value))
                }
              />
            </label>
            <label>
              Completion %
              <input
                type="number"
                min={0}
                max={100}
                value={a.completion_pct ?? ""}
                onChange={(e) =>
                  field(
                    a.id,
                    "completion_pct",
                    e.target.value === "" ? null : Number(e.target.value)
                  )
                }
              />
            </label>
            <label>
              Effectiveness
              <select
                value={a.effectiveness ?? ""}
                onChange={(e) => field(a.id, "effectiveness", e.target.value || null)}
              >
                {EFFECTIVENESS.map((v) => (
                  <option key={v} value={v}>
                    {v === "" ? "—" : v}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Status
              <select value={a.status} onChange={(e) => field(a.id, "status", e.target.value)}>
                {STATUSES.map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="action-foot">
            <button type="button" className="btn small primary" disabled={busy} onClick={() => save(a)}>
              Save action
            </button>
            <button type="button" className="link danger" disabled={busy} onClick={() => remove(a.id)}>
              Delete
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
