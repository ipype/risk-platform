import { useEffect, useState } from "react";
import type { Category, FieldDef, MatrixConfig, Risk } from "../types";
import {
  getCategories,
  getCustomFields,
  getMatrixConfig,
  getRisks,
  deleteRisk,
  exportRegister,
} from "../api";
import { ALL_COLUMNS, DEFAULT_VISIBLE, customColumns, cellText } from "../columns";
import RiskTable from "../components/RiskTable";
import RiskFormPanel from "../components/RiskFormPanel";
import ColumnPicker from "../components/ColumnPicker";
import "../register.css";

const STORAGE_KEY = "risk-register-columns";
const STATUSES = ["Open", "Analyzing", "Mitigating", "Closed"];

function loadVisible(): string[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw) as string[];
  } catch {
    // ignore
  }
  return DEFAULT_VISIBLE;
}

export default function RegisterView() {
  const [categories, setCategories] = useState<Category[]>([]);
  const [config, setConfig] = useState<MatrixConfig | null>(null);
  const [fieldDefs, setFieldDefs] = useState<FieldDef[]>([]);
  const [risks, setRisks] = useState<Risk[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [filterCategory, setFilterCategory] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [visibleKeys, setVisibleKeys] = useState<string[]>(loadVisible);
  const [filters, setFilters] = useState<Record<string, string[] | null>>({});

  const [panelOpen, setPanelOpen] = useState(false);
  const [editing, setEditing] = useState<Risk | null>(null);
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    getCategories().then(setCategories).catch((e) => setError(String(e)));
    getMatrixConfig().then(setConfig).catch((e) => setError(String(e)));
    getCustomFields().then((c) => setFieldDefs(c.fields)).catch(() => setFieldDefs([]));
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

  const allColumns = [...ALL_COLUMNS, ...customColumns(fieldDefs)];
  const visibleColumns = allColumns.filter((c) => visibleKeys.includes(c.key));

  // distinct values per visible column (for the Excel-style filter menus)
  const distinct: Record<string, string[]> = {};
  for (const col of visibleColumns) {
    const set = new Set<string>();
    for (const r of risks) set.add(cellText(r, col));
    distinct[col.key] = Array.from(set).sort((a, b) => a.localeCompare(b));
  }

  // apply per-column filters client-side
  const activeFilters = Object.entries(filters).filter(
    ([, sel]) => sel !== null
  ) as [string, string[]][];
  const displayed =
    activeFilters.length === 0
      ? risks
      : risks.filter((r) =>
          activeFilters.every(([key, sel]) => {
            const col = allColumns.find((c) => c.key === key);
            return col ? sel.includes(cellText(r, col)) : true;
          })
        );

  function onFilter(key: string, selected: string[] | null) {
    setFilters((f) => ({ ...f, [key]: selected }));
  }

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

  async function handleExport() {
    setExporting(true);
    setError(null);
    try {
      await exportRegister();
    } catch (e) {
      setError(`Export failed — ${String(e)}`);
    } finally {
      setExporting(false);
    }
  }

  const filterCount = activeFilters.length;

  return (
    <>
      <header className="topbar">
        <h1>Risk Register</h1>
        <button
          className="btn"
          onClick={handleExport}
          disabled={exporting}
          title="Download the register, mitigation actions, matrix and RBS as an Excel workbook"
        >
          {exporting ? "Exporting…" : "Export to Excel"}
        </button>
        <button className="btn primary" onClick={openCreate}>
          + New risk
        </button>
      </header>

      <div className="toolbar">
        <select value={filterCategory} onChange={(e) => setFilterCategory(e.target.value)}>
          <option value="">All categories</option>
          {categories.map((c) => (
            <option key={c.code} value={c.code}>
              {c.code} — {c.name}
            </option>
          ))}
        </select>
        <select value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)}>
          <option value="">All statuses</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        {filterCount > 0 && (
          <button className="link" onClick={() => setFilters({})}>
            Clear {filterCount} column filter{filterCount === 1 ? "" : "s"}
          </button>
        )}
        <div className="spacer" />
        <ColumnPicker allColumns={allColumns} visibleKeys={visibleKeys} onChange={setVisibleKeys} />
      </div>

      {error && <div className="error">{error}</div>}

      {loading ? (
        <div className="muted">Loading…</div>
      ) : displayed.length === 0 ? (
        <div className="empty">
          {risks.length === 0
            ? "No risks match. Click “+ New risk” to add one."
            : "No risks match the current column filters."}
        </div>
      ) : (
        <RiskTable
          risks={displayed}
          columns={visibleColumns}
          filters={filters}
          distinct={distinct}
          onFilter={onFilter}
          onEdit={openEdit}
          onDelete={handleDelete}
        />
      )}

      <RiskFormPanel
        open={panelOpen}
        editing={editing}
        categories={categories}
        config={config}
        customFields={fieldDefs}
        onClose={() => setPanelOpen(false)}
        onSaved={() => {
          setPanelOpen(false);
          refresh();
        }}
      />
    </>
  );
}
