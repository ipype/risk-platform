import { useState } from "react";

interface Props {
  columnKey: string;
  values: string[];
  selected: string[] | null; // null = all (no filter)
  onChange: (key: string, selected: string[] | null) => void;
}

export default function ColumnFilter({ columnKey, values, selected, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");

  const active = selected !== null && selected.length < values.length;
  const isChecked = (v: string) => (selected === null ? true : selected.includes(v));

  function emit(next: string[]) {
    if (next.length >= values.length) onChange(columnKey, null);
    else onChange(columnKey, next);
  }
  function toggle(v: string) {
    const base = selected === null ? [...values] : [...selected];
    emit(base.includes(v) ? base.filter((x) => x !== v) : [...base, v]);
  }

  const shown = values.filter((v) =>
    (v === "" ? "(empty)" : v).toLowerCase().includes(q.toLowerCase())
  );

  return (
    <span className="colfilter">
      <button
        className={`filter-btn ${active ? "active" : ""}`}
        title="Filter"
        onClick={(e) => {
          e.stopPropagation();
          setOpen((o) => !o);
        }}
      >
        ▾
      </button>
      {open && (
        <div className="filter-menu" onMouseLeave={() => setOpen(false)}>
          <input
            className="filter-search"
            placeholder="Search…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <div className="filter-actions">
            <button className="link" onClick={() => onChange(columnKey, null)}>
              All
            </button>
            <button className="link" onClick={() => onChange(columnKey, [])}>
              None
            </button>
          </div>
          <div className="filter-list">
            {shown.length === 0 ? (
              <div className="muted">No values</div>
            ) : (
              shown.map((v) => (
                <label key={v} className="filter-item">
                  <input type="checkbox" checked={isChecked(v)} onChange={() => toggle(v)} />
                  {v === "" ? "(empty)" : v}
                </label>
              ))
            )}
          </div>
        </div>
      )}
    </span>
  );
}
