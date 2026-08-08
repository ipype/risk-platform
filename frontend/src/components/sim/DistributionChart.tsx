import { useMemo, useState } from "react";
import type { SeriesSummary } from "../../simulation-types";
import { dayToDate, fmtCompactDate, fmtCompactUnits, fmtDate, fmtUnits } from "./format";

/**
 * One simulated quantity, read either way round.
 *
 * The cumulative curve and the density answer different questions and people reach for
 * them in different rooms. The CDF is what a budget comes off — pick a confidence, read a
 * number — and it is the only one of the two that can be read to a decimal. The density is
 * what says *why* that number is where it is: a long right tail, a bimodal shape from a
 * risk that either happens or does not, a spike at the deterministic case when nothing was
 * elicited. A steering committee that only ever sees an S-curve never finds out its P80
 * sits on a shoulder.
 *
 * So both, on one x-axis, with a toggle — and an overlay, because the single most useful
 * reading is where a marked percentile falls relative to the mass. Overlaying costs a
 * second y-axis, which is the price of not making the reader hold one chart in their head
 * while looking at the other.
 *
 * A days series can be read as a calendar date instead. A P80 of "412 days" answers a
 * question nobody asked: a schedule is committed against a date, and making the reader add
 * 412 to a data date they have to go and look up is how a screen full of correct numbers
 * still loses an argument. Elapsed days are calendar days by construction, so the
 * conversion is addition — but it needs day zero, which only the schedule version knows,
 * so the control appears only when the run carries one.
 *
 * Percentile markers are user-taggable rather than fixed at P50/P80. Contract regimes
 * differ — P90 for a sanction case, P50 for an unbiased forecast, P95 where a lender is
 * involved — and a screen that hard-codes the analyst's own convention makes everyone
 * else do arithmetic against a picture. Tagged values are read off the strip beneath the
 * chart, not off the plot: eight labels on a plot 720 units wide collide, and a label that
 * has been nudged to avoid a collision is a label pointing at the wrong place.
 *
 * An added percentile keeps its chip after being unmarked, and carries an explicit delete.
 * Toggling a chip off and having it vanish makes "hide this line" and "I typed 87 by
 * mistake" the same gesture with the same irreversible result, and the reader who wanted
 * the first has to retype the number to get it back.
 *
 * Hand-rolled SVG for the reason the Gantt and the tornado are: `package.json` carries two
 * runtime dependencies, and a charting library would be a third for a polyline, fifty
 * rectangles and some rules.
 */

const W = 720;
const H = 330;
const PAD = { top: 34, right: 60, bottom: 44, left: 78 };
const PLOT_W = W - PAD.left - PAD.right;
const PLOT_H = H - PAD.top - PAD.bottom;

const GRID = [0, 0.2, 0.4, 0.6, 0.8, 1];
const PRESETS = [10, 50, 80, 90, 95];

export type ChartMode = "cdf" | "pdf" | "both";

interface Props {
  series: SeriesSummary;
  /** Percentiles marked on first render. The reader can add and remove afterwards. */
  defaultMarkers?: number[];
  defaultMode?: ChartMode;
  /** Which of the two platform colours this quantity belongs to. */
  accent?: "cost" | "sched";
  title?: string;
  /** Distinguishes the control ids when two charts are on screen at once. */
  idPrefix?: string;
  /**
   * `YYYY-MM-DD` day zero of the network. Set it on a days series and the reader can
   * switch the whole chart onto a calendar axis; leave it off and there is no control,
   * because a date the platform cannot anchor is a date it must not print.
   */
  dayZero?: string | null;
  /** Added to every series value before it becomes a date. */
  dateOffsetDays?: number;
  /** Which reading the chart opens on, when a date reading is available at all. */
  defaultAsDate?: boolean;
}

/**
 * The quantile function, read off the S-curve the engine already sent.
 *
 * `s_curve` is the percentile function sampled on a regular grid — 101 points by default —
 * so interpolating between two of its points is interpolating between two order
 * statistics, which is what `numpy.percentile` does anyway. It agrees with
 * `series.percentiles` to within the grid spacing, and it works for the percentiles the
 * reader invents that were never in the request.
 */
function quantileAt(series: SeriesSummary, p: number): number | null {
  const pts = series.s_curve;
  if (pts.length === 0) return null;
  const target = p / 100;
  if (target <= pts[0].p) return pts[0].x;
  const last = pts[pts.length - 1];
  if (target >= last.p) return last.x;
  for (let i = 1; i < pts.length; i += 1) {
    const a = pts[i - 1];
    const b = pts[i];
    if (target <= b.p) {
      const span = b.p - a.p;
      if (span <= 0) return b.x;
      return a.x + ((target - a.p) / span) * (b.x - a.x);
    }
  }
  return last.x;
}

export default function DistributionChart({
  series,
  defaultMarkers = [50, 80],
  defaultMode = "cdf",
  accent,
  title,
  idPrefix = "dist",
  dayZero = null,
  dateOffsetDays = 0,
  defaultAsDate = false,
}: Props) {
  const [mode, setMode] = useState<ChartMode>(defaultMode);
  const [markers, setMarkers] = useState<number[]>(() =>
    [...new Set(defaultMarkers)].sort((a, b) => a - b)
  );
  // Percentiles the reader typed. Held apart from `markers` so unmarking one leaves the
  // chip in place: hiding a line and discarding a number are different intentions and
  // must not be the same click.
  const [custom, setCustom] = useState<number[]>(() =>
    [...new Set(defaultMarkers)].filter((p) => !PRESETS.includes(p)).sort((a, b) => a - b)
  );
  const [draft, setDraft] = useState("");

  const dateReadable = dayZero != null && series.units === "days";
  const [asDate, setAsDate] = useState(defaultAsDate);
  const onDates = dateReadable && asDate;

  /** The x-axis formatter for whichever reading is live. */
  const fmtX = (v: number | null | undefined) =>
    onDates ? fmtDate(dayToDate(dayZero, (v ?? NaN) + dateOffsetDays)) : fmtUnits(v, series.units);
  const fmtXTick = (v: number) =>
    onDates
      ? fmtCompactDate(dayToDate(dayZero, v + dateOffsetDays))
      : fmtCompactUnits(v, series.units);

  const tone = accent ?? (series.units === "days" ? "sched" : "cost");
  const label = title ?? series.label;

  const bins = useMemo(() => {
    const { edges, counts } = series.histogram;
    if (counts.length === 0 || edges.length < 2) return [];
    const total = counts.reduce((a, b) => a + b, 0) || 1;
    return counts.map((c, i) => ({
      lo: edges[i],
      hi: edges[i + 1],
      /** Share of iterations landing in this bin, not a density: see the caption. */
      rel: c / total,
      count: c,
    }));
  }, [series.histogram]);

  const marked = useMemo(
    () =>
      markers
        .map((p) => ({ p, value: quantileAt(series, p) }))
        .filter((m): m is { p: number; value: number } => m.value != null),
    [markers, series]
  );

  function toggle(p: number) {
    setMarkers((current) =>
      current.includes(p)
        ? current.filter((x) => x !== p)
        : [...current, p].sort((a, b) => a - b)
    );
  }

  /** Drop an added percentile entirely — off the chart and out of the chip row. */
  function remove(p: number) {
    setMarkers((current) => current.filter((x) => x !== p));
    setCustom((current) => current.filter((x) => x !== p));
  }

  function addDraft() {
    const p = Number(draft);
    if (!Number.isFinite(p) || p <= 0 || p >= 100) return;
    // Two decimals is past the resolution of a ten-thousand-iteration run; rounding here
    // stops P80 and P80.0000001 sitting on the chart as two different lines.
    const rounded = Math.round(p * 100) / 100;
    setMarkers((current) =>
      current.includes(rounded) ? current : [...current, rounded].sort((a, b) => a - b)
    );
    if (!PRESETS.includes(rounded)) {
      setCustom((current) =>
        current.includes(rounded) ? current : [...current, rounded].sort((a, b) => a - b)
      );
    }
    setDraft("");
  }

  const points = series.s_curve;
  const showCdf = mode !== "pdf";
  const showPdf = mode !== "cdf";

  if (points.length < 2 && bins.length === 0) {
    return <p className="sim-chart-empty">Not enough points to draw a distribution.</p>;
  }

  const lo = Math.min(points[0]?.x ?? Infinity, bins[0]?.lo ?? Infinity);
  const hi = Math.max(
    points[points.length - 1]?.x ?? -Infinity,
    bins[bins.length - 1]?.hi ?? -Infinity
  );
  // A degenerate series — every iteration identical — would divide by zero and render a
  // vertical line off the plot. One unit of width keeps it drawable and visibly flat.
  const span = hi > lo ? hi - lo : 1;

  const x = (v: number) => PAD.left + ((v - lo) / span) * PLOT_W;
  const yFrac = (f: number) => PAD.top + (1 - f) * PLOT_H;

  const peak = bins.reduce((m, b) => Math.max(m, b.rel), 0);
  const pdfTop = peak > 0 ? peak * 1.08 : 1;

  const path = points
    .map((pt, i) => `${i === 0 ? "M" : "L"}${x(pt.x).toFixed(2)},${yFrac(pt.p).toFixed(2)}`)
    .join(" ");
  const area = `${path} L${x(hi).toFixed(2)},${yFrac(0).toFixed(2)} L${x(lo).toFixed(2)},${yFrac(0).toFixed(2)} Z`;

  const xTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => lo + f * span);
  const leftIsCdf = showCdf;
  const binWidth = bins.length > 0 ? bins[0].hi - bins[0].lo : 0;

  const modeLabel: Record<ChartMode, string> = {
    cdf: "cumulative probability",
    pdf: "probability density",
    both: "density with the cumulative curve over it",
  };

  return (
    <figure className="sim-chart">
      <div className="sim-jcl-controls" role="group" aria-label={`${label} chart view`}>
        <span className="sim-jcl-controls-label">View</span>
        {(["cdf", "pdf", "both"] as ChartMode[]).map((m) => (
          <button
            key={m}
            type="button"
            className={mode === m ? "sim-chip active" : "sim-chip"}
            aria-pressed={mode === m}
            onClick={() => setMode(m)}
          >
            {m === "cdf" ? "CDF" : m === "pdf" ? "PDF" : "Both"}
          </button>
        ))}
      </div>

      {dateReadable && (
        <div className="sim-jcl-controls" role="group" aria-label={`${label} axis reading`}>
          <span className="sim-jcl-controls-label">Read as</span>
          <button
            type="button"
            className={onDates ? "sim-chip" : "sim-chip active"}
            aria-pressed={!onDates}
            onClick={() => setAsDate(false)}
          >
            Days
          </button>
          <button
            type="button"
            className={onDates ? "sim-chip active" : "sim-chip"}
            aria-pressed={onDates}
            onClick={() => setAsDate(true)}
          >
            Date
          </button>
        </div>
      )}

      <div className="sim-jcl-controls" role="group" aria-label={`${label} percentile markers`}>
        <span className="sim-jcl-controls-label">Mark</span>
        {[...new Set([...PRESETS, ...custom, ...markers])]
          .sort((a, b) => a - b)
          .map((p) => {
            const on = markers.includes(p);
            // A preset is furniture and stays; anything the reader introduced can go.
            const removable = !PRESETS.includes(p);
            const chip = (
              <button
                type="button"
                className={on ? "sim-chip active" : "sim-chip"}
                aria-pressed={on}
                onClick={() => toggle(p)}
              >
                P{p}
              </button>
            );
            // Nested buttons are invalid, so the delete is a sibling in a wrapper rather
            // than a child of the chip it belongs to.
            return removable ? (
              <span key={p} className={on ? "sim-chip-group active" : "sim-chip-group"}>
                {chip}
                <button
                  type="button"
                  className="sim-chip-x"
                  aria-label={`Remove the P${p} marker from ${label}`}
                  title={`Remove P${p}`}
                  onClick={() => remove(p)}
                >
                  ×
                </button>
              </span>
            ) : (
              <span key={p} className="sim-chip-slot">
                {chip}
              </span>
            );
          })}
        <input
          className="sim-p-input"
          type="number"
          min={0.1}
          max={99.9}
          step={0.1}
          value={draft}
          placeholder="P…"
          aria-label={`Add a percentile marker to ${label}`}
          id={`${idPrefix}-p-input`}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              addDraft();
            }
          }}
        />
        <button type="button" className="sim-chip" onClick={addDraft} disabled={draft === ""}>
          Add
        </button>
      </div>

      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="sim-svg"
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label={`${label}: ${modeLabel[mode]} from ${fmtX(lo)} to ${fmtX(hi)} over ${series.iterations.toLocaleString()} iterations${
          marked.length > 0
            ? `, marked at ${marked.map((m) => `P${m.p} ${fmtX(m.value)}`).join(", ")}`
            : ""
        }`}
      >
        <title>{label}</title>

        {GRID.map((f) => (
          <g key={`g${f}`}>
            <line x1={PAD.left} x2={W - PAD.right} y1={yFrac(f)} y2={yFrac(f)} className="sim-grid" />
            <text x={PAD.left - 10} y={yFrac(f) + 4} className="sim-axis-text" textAnchor="end">
              {leftIsCdf
                ? `${Math.round(f * 100)}%`
                : `${(f * pdfTop * 100).toFixed(1)}%`}
            </text>
            {mode === "both" && (
              <text
                x={W - PAD.right + 10}
                y={yFrac(f) + 4}
                className="sim-axis-text"
                textAnchor="start"
              >
                {(f * pdfTop * 100).toFixed(1)}%
              </text>
            )}
          </g>
        ))}

        {showPdf &&
          bins.map((b, i) => {
            const bx = x(b.lo);
            const bw = Math.max(x(b.hi) - bx - 0.5, 0.5);
            const bh = pdfTop > 0 ? (b.rel / pdfTop) * PLOT_H : 0;
            if (bh <= 0) return null;
            return (
              <rect
                key={`b${i}`}
                x={bx}
                y={yFrac(0) - bh}
                width={bw}
                height={bh}
                className={`sim-pdf-bar ${tone}`}
              >
                <title>
                  {`${fmtX(b.lo)} – ${fmtX(b.hi)}: ` +
                    `${b.count.toLocaleString()} iterations (${(b.rel * 100).toFixed(2)}%)`}
                </title>
              </rect>
            );
          })}

        {showCdf && mode === "cdf" && <path d={area} className={`sim-curve-fill ${tone}`} />}
        {showCdf && <path d={path} className={`sim-curve-line ${tone}`} />}

        {marked.map((m, i) => (
          <g key={m.p}>
            <line
              x1={x(m.value)}
              x2={x(m.value)}
              y1={PAD.top}
              y2={yFrac(0)}
              className="sim-marker-line"
            />
            {showCdf && (
              <circle
                cx={x(m.value)}
                cy={yFrac(m.p / 100)}
                r={3.5}
                className={`sim-marker-dot ${tone}`}
              />
            )}
            <text
              x={x(m.value)}
              y={PAD.top - 8 - (i % 2) * 13}
              className="sim-marker-text"
              textAnchor={
                m.value > lo + span * 0.85
                  ? "end"
                  : m.value < lo + span * 0.15
                    ? "start"
                    : "middle"
              }
            >
              P{m.p}
            </text>
          </g>
        ))}

        <line x1={PAD.left} x2={PAD.left} y1={PAD.top} y2={H - PAD.bottom} className="sim-axis" />
        <line
          x1={PAD.left}
          x2={W - PAD.right}
          y1={H - PAD.bottom}
          y2={H - PAD.bottom}
          className="sim-axis"
        />

        {xTicks.map((v, i) => (
          <text
            key={`x${i}`}
            x={x(v)}
            y={H - PAD.bottom + 20}
            className="sim-axis-text"
            textAnchor={i === 0 ? "start" : i === xTicks.length - 1 ? "end" : "middle"}
          >
            {fmtXTick(v)}
          </text>
        ))}
      </svg>

      {marked.length > 0 && (
        <dl className="sim-marker-readout">
          {marked.map((m) => (
            <div key={m.p}>
              <dt>P{m.p}</dt>
              <dd>{fmtX(m.value)}</dd>
            </div>
          ))}
        </dl>
      )}

      <figcaption className="sim-chart-caption">
        {label} — {series.iterations.toLocaleString()} iterations, mean{" "}
        {fmtX(series.mean)}, sd {fmtUnits(series.sd, series.units)}.
        {/* The spread stays in days on either reading: a standard deviation is a width,
            and a width has no date. */}
        {onDates && (
          <span className="sim-chart-note">
            Dates are day zero of the schedule plus the simulated elapsed days, rounded to
            the day. Elapsed rather than working days, so a finish landing on a weekend or
            a shutdown is shown where the arithmetic puts it rather than moved to the next
            working morning.
          </span>
        )}
        {showPdf && bins.length > 0 && (
          <span className="sim-chart-note">
            Bars are the share of iterations falling in each of {bins.length} bins{" "}
            {fmtUnits(binWidth, series.units)} wide — a histogram estimate of the density,
            not the density itself, so the heights depend on the bin count. Read shape from
            them and numbers from the curve.
          </span>
        )}
        {mode === "both" && (
          <span className="sim-chart-note">
            Left axis is cumulative probability and belongs to the curve; right axis is the
            per-bin share and belongs to the bars.
          </span>
        )}
      </figcaption>
    </figure>
  );
}
