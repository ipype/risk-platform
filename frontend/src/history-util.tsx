import type { ChangeItem } from "./types";

export function prettyField(field: string): string {
  return field.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function fmtValue(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export function fmtTime(iso: string): string {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function ChangeList({ changes }: { changes: ChangeItem[] | null }) {
  if (!changes || changes.length === 0) return null;
  return (
    <ul className="change-list">
      {changes.map((ch, i) => (
        <li key={i}>
          <span className="change-field">{prettyField(ch.field)}:</span>{" "}
          <span className="change-old">{fmtValue(ch.old)}</span>
          <span className="change-arrow"> → </span>
          <span className="change-new">{fmtValue(ch.new)}</span>
        </li>
      ))}
    </ul>
  );
}
