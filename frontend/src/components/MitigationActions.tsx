import { useEffect, useState } from "react";
import type { DraftAction, MitigationAction, MitigationInput } from "../types";
import { createAction, deleteAction, getActions, updateAction } from "../api";

const STATUSES = ["Proposed", "In progress", "Complete", "Cancelled"];
const EFFECTIVENESS = ["", "Low", "Medium", "High"];

/**
 * The actions panel, in either of two modes.
 *
 * With a `riskId` it is the panel it always was: it fetches, and each card saves itself.
 * With `riskId === null` the risk does not exist yet, so there is nothing to save against
 * and nothing to fetch — the cards are held by the form above and posted with the risk in
 * one request. The alternative was creating the risk silently on first keystroke, which
 * puts a half-finished row in the register the moment somebody changes their mind.
 *
 * One component rather than two because the *fields* are the whole component and they are
 * identical in both modes; the ninety lines of inputs are the thing worth not duplicating.
 * Only add, edit and remove differ, and they are three short branches.
 */
interface Props {
  riskId: number | null;
  /** Draft mode only: the cards, owned by the parent so they survive a re-render. */
  drafts?: DraftAction[];
  onDraftsChange?: (next: DraftAction[]) => void;
}

let nextDraftKey = -1;

export function newDraft(): DraftAction {
  return { key: nextDraftKey--, action: "", status: "Proposed" };
}

interface Row {
  key: number;
  value: MitigationInput;
}

export default function MitigationActions({ riskId, drafts = [], onDraftsChange }: Props) {
  const draftMode = riskId === null;
  const [saved, setSaved] = useState<MitigationAction[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (riskId === null) {
      setSaved([]);
      return;
    }
    getActions(riskId)
      .then(setSaved)
      .catch((e) => setError(String(e)));
  }, [riskId]);

  const rows: Row[] = draftMode
    ? drafts.map((d) => ({ key: d.key, value: d }))
    : saved.map((a) => ({ key: a.id, value: a }));

  function patch<K extends keyof MitigationInput>(
    key: number,
    field: K,
    value: MitigationInput[K]
  ) {
    if (draftMode) {
      onDraftsChange?.(
        drafts.map((d) => (d.key === key ? { ...d, [field]: value } : d))
      );
    } else {
      setSaved((list) =>
        list.map((a) => (a.id === key ? { ...a, [field]: value } : a))
      );
    }
  }

  function add() {
    if (draftMode) {
      onDraftsChange?.([...drafts, newDraft()]);
      return;
    }
    setBusy(true);
    setError(null);
    createAction(riskId as number, { action: "" })
      .then((created) => setSaved((list) => [...list, created]))
      .catch((e) => setError(String(e)))
      .finally(() => setBusy(false));
  }

  async function save(a: MitigationAction) {
    setBusy(true);
    setError(null);
    try {
      const result = await updateAction(a.risk_id, a.id, {
        action: a.action,
        owner: a.owner || null,
        due_date: a.due_date || null,
        budget: a.budget,
        sched_days: a.sched_days,
        completion_pct: a.completion_pct,
        effectiveness: a.effectiveness || null,
        status: a.status,
      });
      setSaved((list) => list.map((x) => (x.id === a.id ? result : x)));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove(key: number) {
    if (draftMode) {
      onDraftsChange?.(drafts.filter((d) => d.key !== key));
      return;
    }
    if (!confirm("Delete this mitigation action?")) return;
    setBusy(true);
    setError(null);
    try {
      await deleteAction(riskId as number, key);
      setSaved((list) => list.filter((x) => x.id !== key));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const blankDrafts = draftMode && drafts.some((d) => !(d.action ?? "").trim());

  return (
    <div className="mit-actions">
      <div className="mit-head">
        <span>Mitigation actions ({rows.length})</span>
        <button type="button" className="btn small" disabled={busy} onClick={add}>
          + Add action
        </button>
      </div>
      {error && <div className="error">{error}</div>}
      {rows.length === 0 && <p className="muted">No actions yet.</p>}
      {draftMode && rows.length > 0 && (
        <p className="muted">
          These save with the risk. Nothing is written until you create it.
        </p>
      )}
      {blankDrafts && (
        <div className="error">
          Every action needs a description, or remove the empty one.
        </div>
      )}

      {rows.map(({ key, value }) => (
        <div className="action-card" key={key}>
          <textarea
            className="action-text"
            placeholder="Describe the mitigation action…"
            value={value.action ?? ""}
            onChange={(e) => patch(key, "action", e.target.value)}
          />
          <div className="action-fields">
            <label>
              Owner
              <input
                value={value.owner ?? ""}
                onChange={(e) => patch(key, "owner", e.target.value)}
              />
            </label>
            <label>
              Due date
              <input
                type="date"
                value={value.due_date ?? ""}
                onChange={(e) => patch(key, "due_date", e.target.value || null)}
              />
            </label>
            <label>
              Budget
              <input
                type="number"
                value={value.budget ?? ""}
                onChange={(e) =>
                  patch(key, "budget", e.target.value === "" ? null : Number(e.target.value))
                }
              />
            </label>
            <label>
              Days consumed
              <input
                type="number"
                min={0}
                value={value.sched_days ?? ""}
                onChange={(e) =>
                  patch(
                    key,
                    "sched_days",
                    e.target.value === "" ? null : Number(e.target.value)
                  )
                }
              />
            </label>
            <label>
              Completion %
              <input
                type="number"
                min={0}
                max={100}
                value={value.completion_pct ?? ""}
                onChange={(e) =>
                  patch(
                    key,
                    "completion_pct",
                    e.target.value === "" ? null : Number(e.target.value)
                  )
                }
              />
            </label>
            <label>
              Effectiveness
              <select
                value={value.effectiveness ?? ""}
                onChange={(e) => patch(key, "effectiveness", e.target.value || null)}
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
              <select
                value={value.status ?? "Proposed"}
                onChange={(e) => patch(key, "status", e.target.value)}
              >
                {STATUSES.map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="action-foot">
            {!draftMode && (
              <button
                type="button"
                className="btn small primary"
                disabled={busy}
                onClick={() => save(value as MitigationAction)}
              >
                Save action
              </button>
            )}
            <button
              type="button"
              className="link danger"
              disabled={busy}
              onClick={() => remove(key)}
            >
              {draftMode ? "Remove" : "Delete"}
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
