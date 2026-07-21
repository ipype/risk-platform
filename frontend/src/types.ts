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
  risk_level: string | null;
  mitigation_actions: string | null;
  owner: string | null;
  last_review_date: string | null;
  comments: string | null;
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
  mitigation_actions?: string | null;
  owner?: string | null;
  last_review_date?: string | null;
  comments?: string | null;
}
export type RiskUpdate = Partial<Omit<RiskCreate, "subcategory_prefix">>;
