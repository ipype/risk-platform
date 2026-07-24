import { useEffect, useState } from "react";
import type { FieldDef } from "../types";
import { getCustomFields, saveCustomFields } from "../api";

const TYPES = ["text", "number", "date", "select"];

interface WorkingField {
  key: string;
  label: string;
  type: string;
  optionsText: string; // comma-separated in the editor
}

export default function CustomFieldsView() {
  const [fields, setFields] = useState<WorkingField[]>([]);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getCustomFields()
      .then((c) =>
        setFields(
          c.fields.map((f) => ({
            key: f.key,
            label: f.label,
            type: f.type,
            optionsText: f.options.join(", "),
          }))
        )
      )
      .catch((e) => setError(String(e)));
  }, []);

  function update(i: number, patch: Partial<WorkingField>) {
    setFields((list) => list.map((f, idx) => (idx === i ? { ...f, ...patch } : f)));
    setMessage(null);
  }
  function add() {
    setFields((list) => [...list, { key: "", label: "New field", type: "text", optionsText: "" }]);
  }
  function remove(i: number) {
    setFields((list) => list.filter((_, idx) => idx !== i));
  }

  async function save() {
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const payload: { fields: FieldDef[] } = {
        fields: fields.map((f) => ({
          key: f.key,
          label: f.label,
          type: f.type,
          options:
            f.type === "select"
              ? f.optionsText.split(",").map((o) => o.trim()).filter(Boolean)
              : [],
        })),
      };
      const saved = await saveCustomFields(payload);
      setFields(
        saved.fields.map((f) => ({
          key: f.key,
          label: f.label,
          type: f.type,
          optionsText: f.options.join(", "),
        }))
      );
      setMessage("Saved. These fields now appear in the risk form and as register columns.");
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="settings">
      <header className="topbar">
        <h1>Custom fields</h1>
        <button className="btn primary" disabled={saving} onClick={save}>
          {saving ? "Saving…" : "Save fields"}
        </button>
      </header>
      {error && <div className="error">{error}</div>}
      {message && <div className="success">{message}</div>}

      <section className="section">
        <p className="muted">
          Add your own fields to every risk. They appear in the risk form and can be turned on
          as register columns from the “Columns” menu.
        </p>
        {fields.map((f, i) => (
          <div className="field-row" key={i}>
            <input
              className="field-label"
              value={f.label}
              placeholder="Field name"
              onChange={(e) => update(i, { label: e.target.value })}
            />
            <select value={f.type} onChange={(e) => update(i, { type: e.target.value })}>
              {TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            {f.type === "select" && (
              <input
                className="field-options"
                value={f.optionsText}
                placeholder="Options, comma-separated"
                onChange={(e) => update(i, { optionsText: e.target.value })}
              />
            )}
            <button className="link danger" onClick={() => remove(i)}>
              Remove
            </button>
          </div>
        ))}
        <button className="btn" onClick={add}>
          + Add field
        </button>
      </section>
    </div>
  );
}
