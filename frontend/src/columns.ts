import type { FieldDef } from "./types";

export interface ColumnDef {
  key: string;
  label: string;
  custom?: boolean;
}

export const ALL_COLUMNS: ColumnDef[] = [
  { key: "risk_code", label: "ID" },
  { key: "title", label: "Risk title" },
  { key: "description", label: "Description" },
  { key: "causes", label: "Causes" },
  { key: "consequences", label: "Consequences" },
  { key: "mitigation_actions", label: "Mitigation notes" },
  { key: "status", label: "Status" },
  { key: "probability", label: "Current probability" },
  { key: "impact", label: "Current impact" },
  { key: "risk_level", label: "Current level" },
  { key: "target_probability", label: "Target probability" },
  { key: "target_impact", label: "Target impact" },
  { key: "target_risk_level", label: "Target level" },
  { key: "owner", label: "Owner" },
  { key: "last_review_date", label: "Last review" },
  { key: "comments", label: "Comments" },
];

export const DEFAULT_VISIBLE: string[] = [
  "risk_code",
  "title",
  "description",
  "causes",
  "consequences",
  "mitigation_actions",
];

export const LEVEL_KEYS = ["risk_level", "target_risk_level"];

export function customColumns(fields: FieldDef[]): ColumnDef[] {
  return fields.map((f) => ({ key: `custom:${f.key}`, label: f.label, custom: true }));
}

import type { Risk } from "./types";

export function cellText(risk: Risk, col: ColumnDef): string {
  if (col.custom) {
    const key = col.key.slice("custom:".length);
    const bag = risk.custom_fields as Record<string, unknown> | null;
    const v = bag ? bag[key] : undefined;
    return v === null || v === undefined ? "" : String(v);
  }
  const v = risk[col.key as keyof Risk];
  return v === null || v === undefined ? "" : String(v);
}
