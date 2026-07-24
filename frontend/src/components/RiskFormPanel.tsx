import { useEffect, useState } from "react";
import type {
  Category,
  FieldDef,
  HistoryEntry,
  MatrixConfig,
  Risk,
  RiskCreate,
  RiskUpdate,
} from "../types";
import { createRisk, getRiskHistory, updateRisk } from "../api";
import { ChangeList, fmtTime } from "../history-util";
import MitigationActions from "./MitigationActions";

interface Props {
  open: boolean;
  editing: Risk | null;
  categories: Category[];
  config: MatrixConfig | null;
  customFields: FieldDef[];
  onClose: () => void;
  onSaved: () => void;
}

const STATUSES = ["Open", "Analyzing", "Mitigating", "Closed"];
const ACTION_LABEL: Record<string, string> = {
  created: "created",
  updated: "edited",
  deleted: "deleted",
};

interface FormState {
  subcategory_prefix: string;
  title: string;
  description: string;
  causes: string;
  consequences: string;
  status: string;
  probability: string;
  impactScores: Record<string, string>;
  targetProbability: string;
  targetImpactScores: Record<string, string>;
  mitigation_actions: string;
  owner: string;
  last_review_date: string;
  comments: string;
  customValues: Record<string, string>;
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
    impactScores: {},
    targetProbability: "",
    targetImpactScores: {},
    mitigation_actions: "",
    owner: "",
    last_review_date: "",
    comments: "",
    customValues: {},
  };
}

function scoresToStr(scores: Record<string, number> | null): Record<string, string> {
  const out: Record<string, string> = {};
  if (scores) for (const [k, v] of Object.entries(scores)) out[k] = String(v);
  return out;
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
    impactScores: scoresToStr(r.impact_scores),
    targetProbability: r.target_probability?.toString() ?? "",
    targetImpactScores: scoresToStr(r.target_impact_scores),
    mitigation_actions: r.mitigation_actions ?? "",
    owner: r.owner ?? "",
    last_review_date: r.last_review_date ?? "",
    comments: r.comments ?? "",
    customValues: (() => {
      const cv: Record<string, string> = {};
      if (r.custom_fields)
        for (const [k, v] of Object.entries(r.custom_fields))
          cv[k] = v === null || v === undefined ? "" : String(v);
      return cv;
    })(),
  };
}

function toNumberScores(scores: Record<string, string>): Record<string, number> {
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(scores)) if (v !== "") out[k] = Number(v);
  return out;
}

export default function RiskFormPanel({
  open,
  editing,
  categories,
  config,
  customFields,
  onClose,
  onSaved,
}: Props) {
  const [form, setForm] = useState<FormState>(emptyForm());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [showHistory, setShowHistory] = useState(false);

  useEffect(() => {
    if (open) {
      setForm(editing ? fromRisk(editing) : emptyForm());
      setError(null);
      setShowHistory(false);
      setHistory([]);
      if (editing) {
        getRiskHistory(editing.id).then(setHistory).catch(() => setHistory([]));
      }
    }
  }, [open, editing]);

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }
  function setCustom(key: string, value: string) {
    setForm((f) => ({ ...f, customValues: { ...f.customValues, [key]: value } }));
  }

  function setScore(bucket: "impactScores" | "targetImpactScores", area: string, value: string) {
    setForm((f) => {
      const next = { ...f[bucket] };
      if (value === "") delete next[area];
      else next[area] = value;
      return { ...f, [bucket]: next };
    });
  }

  const probLevels = config?.probability_levels ?? [];
  const impLevels = config?.impact_levels ?? [];
  const areas = config?.impact_areas ?? [];

  function overallOf(scores: Record<string, string>, fallback: number | null): number | null {
    const vals = Object.values(scores).filter((v) => v !== "").map(Number);
    return vals.length ? Math.max(...vals) : fallback;
  }
  function bandOf(prob: number | null, imp: number | null): string | null {
    if (!config || !prob || !imp) return null;
    const s = prob * imp;
    const b = config.bands.find((x) => s >= x.min_score && s <= x.max_score);
    return b ? b.name : null;
  }

  const curProb = form.probability ? Number(form.probability) : null;
  const curImp = overallOf(form.impactScores, editing?.impact ?? null);
  const curBand = bandOf(curProb, curImp);
  const tgtProb = form.targetProbability ? Number(form.targetProbability) : null;
  const tgtImp = overallOf(form.targetImpactScores, editing?.target_impact ?? null);
  const tgtBand = bandOf(tgtProb, tgtImp);

  async function handleSave() {
    setSaving(true);
    setError(null);
    const common = {
      title: form.title,
      description: form.description || null,
      causes: form.causes || null,
      consequences: form.consequences || null,
      status: form.status,
      probability: form.probability ? Number(form.probability) : null,
      impact_scores: toNumberScores(form.impactScores),
      impact: editing ? editing.impact : null,
      target_probability: form.targetProbability ? Number(form.targetProbability) : null,
      target_impact_scores: toNumberScores(form.targetImpactScores),
      target_impact: editing ? editing.target_impact : null,
      mitigation_actions: form.mitigation_actions || null,
      owner: form.owner || null,
      last_review_date: form.last_review_date || null,
      comments: form.comments || null,
      custom_fields: (() => {
        const cf: Record<string, unknown> = {};
        for (const f of customFields) {
          const raw = form.customValues[f.key] ?? "";
          if (raw === "") continue;
          cf[f.key] = f.type === "number" ? Number(raw) : raw;
        }
        return cf;
      })(),
    };
    try {
      if (editing) {
        await updateRisk(editing.id, common as RiskUpdate);
      } else {
        await createRisk({ subcategory_prefix: form.subcategory_prefix, ...common } as RiskCreate);
      }
      onSaved();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  const canSave = form.title.trim() !== "" && (editing !== null || form.subcategory_prefix !== "");

  function impactBlock(
    title: string,
    probValue: string,
    onProb: (v: string) => void,
    bucket: "impactScores" | "targetImpactScores",
    scores: Record<string, string>,
    overall: number | null,
    band: string | null
  ) {
    return (
      <div className="impact-areas">
        <div className="impact-head">{title}</div>
        <label>
          Probability
          <select value={probValue} onChange={(e) => onProb(e.target.value)}>
            <option value="">Not assessed</option>
            {probLevels.map((l) => (
              <option key={l.level} value={l.level}>
                {l.level} · {l.label}
              </option>
            ))}
          </select>
        </label>
        {areas.map((a) => (
          <label key={a.code}>
            {a.name}
            <select value={scores[a.code] ?? ""} onChange={(e) => setScore(bucket, a.code, e.target.value)}>
              <option value="">Not applicable</option>
              {impLevels.map((l) => {
                const desc = a.descriptors[String(l.level)];
                return (
                  <option key={l.level} value={l.level}>
                    {l.level} · {l.label}{desc ? ` — ${desc}` : ""}
                  </option>
                );
              })}
            </select>
          </label>
        ))}
        <div className="overall-line">
          Overall impact: <strong>{overall ?? "—"}</strong>
          {band ? ` · ${band}` : ""}
        </div>
      </div>
    );
  }

  return (
    <>
      <div className={`backdrop ${open ? "show" : ""}`} onClick={onClose} />
      <aside className={`panel ${open ? "open" : ""}`}>
        <div className="panel-head">
          <h2>{editing ? `Edit ${editing.risk_code}` : "New risk"}</h2>
          <button className="link" onClick={onClose}>Close</button>
        </div>

        <div className="panel-body">
          {editing ? (
            <div className="readonly">Risk ID: <strong>{editing.risk_code}</strong></div>
          ) : (
            <label>
              Subcategory
              <select value={form.subcategory_prefix} onChange={(e) => set("subcategory_prefix", e.target.value)}>
                <option value="">Select…</option>
                {categories.map((c) => (
                  <optgroup key={c.code} label={`${c.code} — ${c.name}`}>
                    {c.subcategories.map((s) => (
                      <option key={s.prefix} value={s.prefix}>{s.prefix} — {s.name}</option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </label>
          )}

          <label>Risk title<input value={form.title} onChange={(e) => set("title", e.target.value)} /></label>
          <label>Description<textarea value={form.description} onChange={(e) => set("description", e.target.value)} /></label>
          <label>Causes<textarea value={form.causes} onChange={(e) => set("causes", e.target.value)} /></label>
          <label>Consequences<textarea value={form.consequences} onChange={(e) => set("consequences", e.target.value)} /></label>
          <label>Mitigation notes (summary)<textarea value={form.mitigation_actions} onChange={(e) => set("mitigation_actions", e.target.value)} /></label>

          {impactBlock("Current assessment", form.probability, (v) => set("probability", v), "impactScores", form.impactScores, curImp, curBand)}
          {impactBlock("Target (residual) assessment", form.targetProbability, (v) => set("targetProbability", v), "targetImpactScores", form.targetImpactScores, tgtImp, tgtBand)}

          {editing ? (
            <MitigationActions riskId={editing.id} />
          ) : (
            <p className="muted">Save the risk first, then reopen it to add mitigation actions.</p>
          )}

          <div className="row">
            <label>
              Status
              <select value={form.status} onChange={(e) => set("status", e.target.value)}>
                {STATUSES.map((s) => (<option key={s} value={s}>{s}</option>))}
              </select>
            </label>
            <label>Owner<input value={form.owner} onChange={(e) => set("owner", e.target.value)} /></label>
          </div>
          <label>Last review<input type="date" value={form.last_review_date} onChange={(e) => set("last_review_date", e.target.value)} /></label>
          <label>Comments<textarea value={form.comments} onChange={(e) => set("comments", e.target.value)} /></label>

          {customFields.length > 0 && (
            <div className="impact-areas">
              <div className="impact-head">Custom fields</div>
              {customFields.map((f) => (
                <label key={f.key}>
                  {f.label}
                  {f.type === "select" ? (
                    <select value={form.customValues[f.key] ?? ""} onChange={(e) => setCustom(f.key, e.target.value)}>
                      <option value="">—</option>
                      {f.options.map((o) => (
                        <option key={o} value={o}>{o}</option>
                      ))}
                    </select>
                  ) : (
                    <input type={f.type === "number" ? "number" : f.type === "date" ? "date" : "text"} value={form.customValues[f.key] ?? ""} onChange={(e) => setCustom(f.key, e.target.value)} />
                  )}
                </label>
              ))}
            </div>
          )}

          {editing && (
            <div className="history-block">
              <button type="button" className="link history-toggle" onClick={() => setShowHistory((s) => !s)}>
                {showHistory ? "▾" : "▸"} History ({history.length})
              </button>
              {showHistory && (history.length === 0 ? (
                <p className="muted">No changes recorded.</p>
              ) : (
                <ul className="history-list">
                  {history.map((h) => (
                    <li key={h.id} className={`history-item action-${h.action}`}>
                      <div className="history-head">
                        <strong>{h.actor}</strong> {ACTION_LABEL[h.action] ?? h.action}
                        <span className="history-time">{fmtTime(h.created_at)}</span>
                      </div>
                      <ChangeList changes={h.changes} />
                    </li>
                  ))}
                </ul>
              ))}
            </div>
          )}

          {error && <div className="error">{error}</div>}
        </div>

        <div className="panel-foot">
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={saving || !canSave} onClick={handleSave}>
            {saving ? "Saving…" : editing ? "Save changes" : "Create risk"}
          </button>
        </div>
      </aside>
    </>
  );
}
