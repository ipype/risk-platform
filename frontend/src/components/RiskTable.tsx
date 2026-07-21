import type { Risk } from "../types";
import type { ColumnDef } from "../columns";

interface Props {
  risks: Risk[];
  columns: ColumnDef[];
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

function cellValue(risk: Risk, key: keyof Risk): string {
  const v = risk[key];
  return v === null || v === undefined ? "" : String(v);
}

export default function RiskTable({ risks, columns, onEdit, onDelete }: Props) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key}>{c.label}</th>
            ))}
            <th className="actions-col">Actions</th>
          </tr>
        </thead>
        <tbody>
          {risks.map((risk) => (
            <tr key={risk.id}>
              {columns.map((c) => {
                if (c.key === "risk_level") {
                  const level = risk.risk_level;
                  return (
                    <td key={c.key}>
                      {level ? (
                        <span
                          className={`badge level-${level
                            .replace(/\s+/g, "-")
                            .toLowerCase()}`}
                        >
                          {level}
                        </span>
                      ) : (
                        ""
                      )}
                    </td>
                  );
                }
                if (c.key === "status") {
                  return (
                    <td key={c.key}>
                      <span className={`badge status-${risk.status.toLowerCase()}`}>
                        {risk.status}
                      </span>
                    </td>
                  );
                }
                const long = LONG_FIELDS.includes(c.key);
                const value = cellValue(risk, c.key);
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
