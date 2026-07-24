export interface Subcategory {
  code: string;
  name: string;
  prefix: string;
}
export interface Category {
  code: string;
  name: string;
  subcategories: Subcategory[];
}
export interface Risk {
  id: number;
  risk_code: string;
  title: string;
  description: string | null;
  causes: string | null;
  consequences: string | null;
  status: string;
  probability: number | null;
  impact: number | null;
  impact_scores: Record<string, number> | null;
  risk_level: string | null;
  target_probability: number | null;
  target_impact: number | null;
  target_impact_scores: Record<string, number> | null;
  target_risk_level: string | null;
  mitigation_actions: string | null;
  owner: string | null;
  last_review_date: string | null;
  comments: string | null;
  custom_fields: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}
export interface RiskCreate {
  subcategory_prefix: string;
  title: string;
  description?: string | null;
  causes?: string | null;
  consequences?: string | null;
  status?: string;
  probability?: number | null;
  impact?: number | null;
  impact_scores?: Record<string, number> | null;
  target_probability?: number | null;
  target_impact?: number | null;
  target_impact_scores?: Record<string, number> | null;
  mitigation_actions?: string | null;
  owner?: string | null;
  last_review_date?: string | null;
  comments?: string | null;
  custom_fields?: Record<string, unknown> | null;
}
export type RiskUpdate = Partial<Omit<RiskCreate, "subcategory_prefix">>;

export interface LevelDef {
  level: number;
  label: string;
}
export interface AreaDef {
  code: string;
  name: string;
  descriptors: Record<string, string>;
}
export interface BandDef {
  name: string;
  min_score: number;
  max_score: number;
  color: string;
}
export interface MatrixConfig {
  name: string;
  probability_levels: LevelDef[];
  impact_levels: LevelDef[];
  impact_areas: AreaDef[];
  bands: BandDef[];
}

export interface ChangeItem {
  field: string;
  old: unknown;
  new: unknown;
}
export interface HistoryEntry {
  id: number;
  risk_id: number;
  risk_code: string;
  action: string;
  actor: string;
  changes: ChangeItem[] | null;
  created_at: string;
}

export interface MitigationAction {
  id: number;
  risk_id: number;
  action: string;
  owner: string | null;
  due_date: string | null;
  budget: number | null;
  completion_pct: number | null;
  effectiveness: string | null;
  status: string;
  sort_order: number;
  created_at: string;
  updated_at: string;
}
export interface MitigationInput {
  action?: string;
  owner?: string | null;
  due_date?: string | null;
  budget?: number | null;
  completion_pct?: number | null;
  effectiveness?: string | null;
  status?: string;
}

export interface FieldDef {
  key: string;
  label: string;
  type: string;
  options: string[];
}
export interface CustomFieldConfig {
  fields: FieldDef[];
}
