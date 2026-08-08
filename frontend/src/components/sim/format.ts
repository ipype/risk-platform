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

/* --------------------------------------------------------------------------------- *
 * calendar dates
 *
 * Every day number the engine returns is elapsed days from day zero of the parsed
 * network, and elapsed days are calendar days by construction — that conversion is the
 * whole reason the engine works in them. So a finish day becomes a date by plain
 * addition, with no calendar to walk and no working week to honour.
 *
 * All of it in UTC. The anchor arrives as a bare `YYYY-MM-DD` with no time and no zone,
 * and parsing that through the local zone puts a reader west of Greenwich a day early on
 * every date the screen prints. Nothing here is a moment in time; these are dates.
 * --------------------------------------------------------------------------------- */

const DAY_MS = 86_400_000;

const dateFmt = new Intl.DateTimeFormat(undefined, {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});
const compactDateFmt = new Intl.DateTimeFormat(undefined, {
  day: "numeric",
  month: "short",
  timeZone: "UTC",
});

/** `YYYY-MM-DD` to a UTC midnight, or null if it is not one. */
export function parseDay(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return null;
  const t = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return Number.isFinite(t) ? t : null;
}

/** Day zero plus `day` elapsed days. Fractions round to the nearest whole date. */
export function dayToDate(
  dayZero: string | null | undefined,
  day: number | null | undefined
): Date | null {
  const base = parseDay(dayZero);
  if (base == null || day == null || !Number.isFinite(day)) return null;
  return new Date(base + Math.round(day) * DAY_MS);
}

/** The inverse: whole elapsed days from day zero to `iso`. */
export function dateToDay(
  dayZero: string | null | undefined,
  iso: string | null | undefined
): number | null {
  const base = parseDay(dayZero);
  const at = parseDay(iso);
  if (base == null || at == null) return null;
  return Math.round((at - base) / DAY_MS);
}

/** `YYYY-MM-DD`, which is what a native date input wants back. */
export function toIsoDay(date: Date | null | undefined): string {
  if (date == null || Number.isNaN(date.getTime())) return "";
  return date.toISOString().slice(0, 10);
}

export function fmtDate(date: Date | null | undefined): string {
  if (date == null || Number.isNaN(date.getTime())) return "—";
  return dateFmt.format(date);
}

/** Axis ticks, where the year will not fit four times across a plot. */
export function fmtCompactDate(date: Date | null | undefined): string {
  if (date == null || Number.isNaN(date.getTime())) return "—";
  return compactDateFmt.format(date);
}
