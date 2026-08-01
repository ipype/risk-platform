/**
 * Formatting for simulated quantities.
 *
 * No currency symbol anywhere. The platform stores elicited magnitudes as plain numbers
 * and has never been told which currency a project is in; printing a `$` would be the
 * screen inventing one. Units come from `SeriesSummary.units`, which the engine sets.
 */

const money = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
const compact = new Intl.NumberFormat(undefined, {
  notation: "compact",
  maximumFractionDigits: 1,
});
const days = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });
const percent = new Intl.NumberFormat(undefined, {
  style: "percent",
  maximumFractionDigits: 1,
});

export function fmtMoney(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return money.format(value);
}

/** Axis labels, where four full-length numbers will not fit side by side. */
export function fmtCompact(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return compact.format(value);
}

export function fmtDays(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${days.format(value)} d`;
}

export function fmtPercent(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return percent.format(value);
}

export function fmtUnits(value: number | null | undefined, units: string): string {
  return units === "days" ? fmtDays(value) : fmtMoney(value);
}

export function fmtDuration(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms} ms`;
  const seconds = ms / 1000;
  if (seconds < 90) return `${days.format(seconds)} s`;
  return `${days.format(seconds / 60)} min`;
}
