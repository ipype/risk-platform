import { useEffect, useState } from "react";
import type { Category, Risk } from "./types";
import { getCategories, getRisks, deleteRisk } from "./api";
import { ALL_COLUMNS, DEFAULT_VISIBLE } from "./columns";
import RiskTable from "./components/RiskTable";
import RiskFormPanel from "./components/RiskFormPanel";
import ColumnPicker from "./components/ColumnPicker";

const STORAGE_KEY = "risk-register-columns";
const STATUSES = ["Open", "Analyzing", "Mitigating", "Closed"];

function loadVisible(): string[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw) as string[];
  } catch {
    // ignore bad storage
  }
  return DEFAULT_VISIBLE;
}

export default function App() {
  const [categories, setCategories] = useState<Category[]>([]);
  const [risks, setRisks] = useState<Risk[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [filterCategory, setFilterCategory] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [visibleKeys, setVisibleKeys] = useState<string[]>(loadVisible);

  const [panelOpen, setPanelOpen] = useState(false);
  const [editing, setEditing] = useState<Risk | null>(null);

  useEffect(() => {
    getCategories()
      .then(setCategories)
      .catch((e) => setError(String(e)));
  }, []);

  function refresh() {
    setLoading(true);
    getRisks({
      category: filterCategory || undefined,
      status: filterStatus || undefined,
    })
      .then((data) => {
        setRisks(data);
        setError(null);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }

  useEffect(refresh, [filterCategory, filterStatus]);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(visibleKeys));
  }, [visibleKeys]);

  const visibleColumns = ALL_COLUMNS.filter((c) => visibleKeys.includes(c.key));

  function openCreate() {
    setEditing(null);
    setPanelOpen(true);
  }
  function openEdit(risk: Risk) {
    setEditing(risk);
    setPanelOpen(true);
  }
  async function handleDelete(risk: Risk) {
    if (!confirm(`Delete ${risk.risk_code}? This cannot be undone.`)) return;
    try {
      await deleteRisk(risk.id);
      refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div className="app">
      <header className="topbar">
        <h1>Risk Register</h1>
        <button className="btn primary" onClick={openCreate}>
          + New risk
        </button>
      </header>

      <div className="toolbar">
        <select
          value={filterCategory}
          onChange={(e) => setFilterCategory(e.target.value)}
        >
          <option value="">All categories</option>
          {categories.map((c) => (
            <option key={c.code} value={c.code}>
              {c.code} — {c.name}
            </option>
          ))}
        </select>
        <select
          value={filterStatus}
          onChange={(e) => setFilterStatus(e.target.value)}
        >
          <option value="">All statuses</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <div className="spacer" />
        <ColumnPicker
          allColumns={ALL_COLUMNS}
          visibleKeys={visibleKeys}
          onChange={setVisibleKeys}
        />
      </div>

      {error && <div className="error">{error}</div>}

      {loading ? (
        <div className="muted">Loading…</div>
      ) : risks.length === 0 ? (
        <div className="empty">No risks match. Click “+ New risk” to add one.</div>
      ) : (
        <RiskTable
          risks={risks}
          columns={visibleColumns}
          onEdit={openEdit}
          onDelete={handleDelete}
        />
      )}

      <RiskFormPanel
        open={panelOpen}
        editing={editing}
        categories={categories}
        onClose={() => setPanelOpen(false)}
        onSaved={() => {
          setPanelOpen(false);
          refresh();
        }}
      />
    </div>
  );
}
