import { useEffect, useState } from "react";
import type { Category, Risk, RiskCreate, RiskUpdate } from "../types";
import { createRisk, updateRisk } from "../api";

interface Props {
  open: boolean;
  editing: Risk | null;
  categories: Category[];
  onClose: () => void;
  onSaved: () => void;
}

const STATUSES = ["Open", "Analyzing", "Mitigating", "Closed"];

interface FormState {
  subcategory_prefix: string;
  title: string;
  description: string;
  causes: string;
  consequences: string;
  status: string;
  probability: string;
  impact: string;
  mitigation_actions: string;
  owner: string;
  last_review_date: string;
  comments: string;
}

function emptyForm(): FormState {
  return {
    subcategory_prefix: "",
    title: "",
    description: "",
    causes: "",
    consequences: "",
    status: "Open",
    probability: "",
    impact: "",
    mitigation_actions: "",
    owner: "",
    last_review_date: "",
    comments: "",
  };
}

function fromRisk(r: Risk): FormState {
  return {
    subcategory_prefix: r.risk_code.split("-").slice(0, 2).join("-"),
    title: r.title,
    description: r.description ?? "",
    causes: r.causes ?? "",
    consequences: r.consequences ?? "",
    status: r.status,
    probability: r.probability?.toString() ?? "",
    impact: r.impact?.toString() ?? "",
    mitigation_actions: r.mitigation_actions ?? "",
    owner: r.owner ?? "",
    last_review_date: r.last_review_date ?? "",
    comments: r.comments ?? "",
  };
}

function numOrNull(v: string): number | null {
  if (v.trim() === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

export default function RiskFormPanel({
  open,
  editing,
  categories,
  onClose,
  onSaved,
}: Props) {
  const [form, setForm] = useState<FormState>(emptyForm());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setForm(editing ? fromRisk(editing) : emptyForm());
      setError(null);
    }
  }, [open, editing]);

  function set<K extends keyof FormState>(key: K, value: string) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function handleSave() {
    setSaving(true);
    setError(null);
    const common = {
      title: form.title,
      description: form.description || null,
      causes: form.causes || null,
      consequences: form.consequences || null,
      status: form.status,
      probability: numOrNull(form.probability),
      impact: numOrNull(form.impact),
      mitigation_actions: form.mitigation_actions || null,
      owner: form.owner || null,
      last_review_date: form.last_review_date || null,
      comments: form.comments || null,
    };
    try {
      if (editing) {
        await updateRisk(editing.id, common as RiskUpdate);
      } else {
        await createRisk({
          subcategory_prefix: form.subcategory_prefix,
          ...common,
        } as RiskCreate);
      }
      onSaved();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  const canSave = form.title.trim() !== "" && (editing !== null || form.subcategory_prefix !== "");

  return (
    <>
      <div className={`backdrop ${open ? "show" : ""}`} onClick={onClose} />
      <aside className={`panel ${open ? "open" : ""}`}>
        <div className="panel-head">
          <h2>{editing ? `Edit ${editing.risk_code}` : "New risk"}</h2>
          <button className="link" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="panel-body">
          {editing ? (
            <div className="readonly">
              Risk ID: <strong>{editing.risk_code}</strong>
            </div>
          ) : (
            <label>
              Subcategory
              <select
                value={form.subcategory_prefix}
                onChange={(e) => set("subcategory_prefix", e.target.value)}
              >
                <option value="">Select…</option>
                {categories.map((c) => (
                  <optgroup key={c.code} label={`${c.code} — ${c.name}`}>
                    {c.subcategories.map((s) => (
                      <option key={s.prefix} value={s.prefix}>
                        {s.prefix} — {s.name}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </label>
          )}

          <label>
            Risk title
            <input
              value={form.title}
              onChange={(e) => set("title", e.target.value)}
            />
          </label>
          <label>
            Description
            <textarea
              value={form.description}
              onChange={(e) => set("description", e.target.value)}
            />
          </label>
          <label>
            Causes
            <textarea
              value={form.causes}
              onChange={(e) => set("causes", e.target.value)}
            />
          </label>
          <label>
            Consequences
            <textarea
              value={form.consequences}
              onChange={(e) => set("consequences", e.target.value)}
            />
          </label>
          <label>
            Mitigation actions
            <textarea
              value={form.mitigation_actions}
              onChange={(e) => set("mitigation_actions", e.target.value)}
            />
          </label>

          <div className="row">
            <label>
              Status
              <select
                value={form.status}
                onChange={(e) => set("status", e.target.value)}
              >
                {STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Probability
              <input
                type="number"
                min={1}
                max={5}
                value={form.probability}
                onChange={(e) => set("probability", e.target.value)}
              />
            </label>
            <label>
              Impact
              <input
                type="number"
                min={1}
                max={5}
                value={form.impact}
                onChange={(e) => set("impact", e.target.value)}
              />
            </label>
          </div>

          <div className="row">
            <label>
              Owner
              <input
                value={form.owner}
                onChange={(e) => set("owner", e.target.value)}
              />
            </label>
            <label>
              Last review
              <input
                type="date"
                value={form.last_review_date}
                onChange={(e) => set("last_review_date", e.target.value)}
              />
            </label>
          </div>
          <label>
            Comments
            <textarea
              value={form.comments}
              onChange={(e) => set("comments", e.target.value)}
            />
          </label>

          {error && <div className="error">{error}</div>}
        </div>

        <div className="panel-foot">
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            className="btn primary"
            disabled={saving || !canSave}
            onClick={handleSave}
          >
            {saving ? "Saving…" : editing ? "Save changes" : "Create risk"}
          </button>
        </div>
      </aside>
    </>
  );
}
