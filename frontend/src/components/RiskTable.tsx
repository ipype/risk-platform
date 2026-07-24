import type { Risk } from "../types";
import type { ColumnDef } from "../columns";
import { LEVEL_KEYS, cellText } from "../columns";
import ColumnFilter from "./ColumnFilter";

interface Props {
  risks: Risk[];
  columns: ColumnDef[];
  filters: Record<string, string[] | null>;
  distinct: Record<string, string[]>;
  onFilter: (key: string, selected: string[] | null) => void;
  onEdit: (r: Risk) => void;
  onDelete: (r: Risk) => void;
}

const LONG_FIELDS = [
  "description",
  "causes",
  "consequences",
  "mitigation_actions",
  "comments",
];

function levelClass(level: string): string {
  return `badge level-${level.replace(/\s+/g, "-").toLowerCase()}`;
}

export default function RiskTable({
  risks,
  columns,
  filters,
  distinct,
  onFilter,
  onEdit,
  onDelete,
}: Props) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key}>
                <div className="th-inner">
                  <span>{c.label}</span>
                  <ColumnFilter
                    columnKey={c.key}
                    values={distinct[c.key] ?? []}
                    selected={filters[c.key] ?? null}
                    onChange={onFilter}
                  />
                </div>
              </th>
            ))}
            <th className="actions-col">Actions</th>
          </tr>
        </thead>
        <tbody>
          {risks.map((risk) => (
            <tr key={risk.id}>
              {columns.map((c) => {
                const value = cellText(risk, c);
                if (!c.custom && LEVEL_KEYS.includes(c.key)) {
                  return (
                    <td key={c.key}>
                      {value ? <span className={levelClass(value)}>{value}</span> : ""}
                    </td>
                  );
                }
                if (!c.custom && c.key === "status") {
                  return (
                    <td key={c.key}>
                      <span className={`badge status-${risk.status.toLowerCase()}`}>
                        {risk.status}
                      </span>
                    </td>
                  );
                }
                const long = LONG_FIELDS.includes(c.key);
                return (
                  <td key={c.key} className={long ? "cell-long" : ""} title={value}>
                    {value}
                  </td>
                );
              })}
              <td className="actions-col">
                <button className="link" onClick={() => onEdit(risk)}>
                  Edit
                </button>
                <button className="link danger" onClick={() => onDelete(risk)}>
                  Delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
