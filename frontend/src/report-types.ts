/**
 * Types for the structured report endpoints.
 *
 * Only the section manifest is modelled. The document itself is never parsed on this
 * side — the HTML rendering is what the screen shows and the workbook is what gets
 * downloaded, so mirroring the block union here would be a second declaration of a shape
 * the server already owns, drifting the first time a block kind is added.
 */

/** Which set of scores the matrix section is drawn from. */
export type ReportBasis = "current" | "target";

export interface ReportSectionOption {
  id: string;
  title: string;
  /** One line for the picker: what this section answers. */
  summary: string;
  available: boolean;
  /** Present only when unavailable. Shown as-is. */
  reason: string | null;
}

export interface ReportSectionsResponse {
  scope_id: number | null;
  run_id: number | null;
  generated_on: string;
  sections: ReportSectionOption[];
  /** Findings from the read itself — e.g. a requested scope that the run overrode. */
  notes: string[];
}

/**
 * Everything that identifies one report.
 *
 * `sections` is `null` for "every available section", which is not the same request as
 * an empty array — that one asks for nothing and the API refuses it.
 */
export interface ReportQuery {
  title: string;
  prepared_by: string;
  currency: string;
  run_id: number | null;
  roi_id: number | null;
  plan_id: number | null;
  lens?: string;
  basis?: ReportBasis;
  sections: string[] | null;
}

export const DEFAULT_REPORT: ReportQuery = {
  title: "Quantitative risk analysis report",
  prepared_by: "",
  currency: "$",
  run_id: null,
  roi_id: null,
  plan_id: null,
  sections: null,
};
