/**
 * Formatting for simulated quantities.
 *
 * The currency symbol comes from `config.CURRENCY`, one read, never a literal here. See
 * that constant for why the platform prints a symbol at all when it has no per-project
 * currency field to read one from.
 *
 * Negatives print as `-$1,200`, not `($1,200)`. A risk with a negative contribution is a
 * measured result — an opportunity, or a variability row sampled below its reference —
 * and accountancy brackets read as an error state next to a table of positive numbers.
 * The sign goes outside the symbol so it survives a change of currency.
 *
 * Non-currency units come from `SeriesSummary.units`, which the engine sets. Nothing here
 * guesses: `fmtUnits` and `fmtCompactUnits` are handed the units, and a caller that does
 * not have them should be using `fmtMoney` or `fmtDays` directly and saying which it means.
 */

import { CURRENCY } from "../../config";

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

/** `-$1,200`. Sign outside the symbol, magnitude formatted for the reader's locale. */
function withSymbol(value: number, formatted: string): string {
  return value < 0
    ? `-${CURRENCY}${formatted.replace(/^-/, "")}`
    : `${CURRENCY}${formatted}`;
}

export function fmtMoney(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return withSymbol(value, money.format(value));
}

/** Axis labels, where four full-length numbers will not fit side by side. */
export function fmtCompact(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return compact.format(value);
}

/** The same, with the currency symbol. Axis ticks on a money axis. */
export function fmtCompactMoney(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return withSymbol(value, compact.format(value));
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

/**
 * Axis-width formatting that still says which axis it is.
 *
 * A tick reading `1.2M` on a cost axis and `1.2` on a delay axis is the one place the
 * missing symbol actually misleads rather than merely omits, because both axes are on
 * screen at once in the joint view.
 */
export function fmtCompactUnits(
  value: number | null | undefined,
  units: string
): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return units === "days" ? `${days.format(value)}d` : fmtCompactMoney(value);
}

export function fmtDuration(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms} ms`;
  const seconds = ms / 1000;
  if (seconds < 90) return `${days.format(seconds)} s`;
  return `${days.format(seconds / 60)} min`;
}
