import { useState } from "react";
import type { ColumnDef } from "../columns";

interface Props {
  allColumns: ColumnDef[];
  visibleKeys: string[];
  onChange: (keys: string[]) => void;
}

export default function ColumnPicker({
  allColumns,
  visibleKeys,
  onChange,
}: Props) {
  const [open, setOpen] = useState(false);

  function toggle(key: string) {
    if (visibleKeys.includes(key)) {
      onChange(visibleKeys.filter((k) => k !== key));
    } else {
      // keep the canonical column order
      onChange(
        allColumns
          .map((c) => c.key as string)
          .filter((k) => k === key || visibleKeys.includes(k))
      );
    }
  }

  return (
    <div className="colpicker">
      <button className="btn" onClick={() => setOpen((o) => !o)}>
        Columns ▾
      </button>
      {open && (
        <div className="colpicker-menu" onMouseLeave={() => setOpen(false)}>
          {allColumns.map((c) => (
            <label key={c.key} className="colpicker-item">
              <input
                type="checkbox"
                checked={visibleKeys.includes(c.key)}
                onChange={() => toggle(c.key)}
              />
              {c.label}
            </label>
          ))}
        </div>
      )}
    </div>
  );
}
