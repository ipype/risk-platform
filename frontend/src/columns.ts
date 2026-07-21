import type { Risk } from "./types";

export interface ColumnDef {
  key: keyof Risk;
  label: string;
}

export const ALL_COLUMNS: ColumnDef[] = [
  { key: "risk_code", label: "ID" },
  { key: "title", label: "Risk title" },
  { key: "description", label: "Description" },
  { key: "causes", label: "Causes" },
  { key: "consequences", label: "Consequences" },
  { key: "mitigation_actions", label: "Mitigation actions" },
  { key: "status", label: "Status" },
  { key: "probability", label: "Probability" },
  { key: "impact", label: "Impact" },
  { key: "risk_level", label: "Risk level" },
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
