import type { ReactNode } from "react";
import { formatValue, previewShape } from "../../quant/curve";
import type { DimensionSummary, QuantPoint } from "../../quant/types";

/**
 * What the numbers look like once sampled.
 *
 * The single cheapest quality lift in elicitation. An SME who watches the shape move while
 * they type revises their numbers; one who only ever sees three input boxes does not. It is
 * worth the round trip on every pause.
 *
 * When the bounds have been widened — trigen, or any percentile reading — the elicited
 * values are drawn as ticks inside the recovered support. Without them the form looks like
 * it ignored what was typed, and the analyst's first instinct is to distrust the tool
 * rather than to notice it restored the tail on purpose.
 */

const W = 280;
const H = 92;
const PAD_X = 10;
const TOP = 10;
const BASE = 74;

interface Props {
  summary: DimensionSummary;
  points: QuantPoint[] | null;
  unit: string;
}

export default function DistributionPreview({ summary, points, unit }: Props) {
  const shape = previewShape(summary, points);
  const span = summary.hi - summary.lo;

  const toX = (v: number): number =>
    span > 0 ? PAD_X + ((v - summary.lo) / span) * (W - PAD_X * 2) : W / 2;
  const toY = (y: number): number => BASE - y * (BASE - TOP);

  let body: ReactNode = null;

  if (shape?.kind === "curve") {
    const d = shape.points
      .map(([x, y], i) => `${i === 0 ? "M" : "L"}${toX(x).toFixed(2)} ${toY(y).toFixed(2)}`)
      .join(" ");
    const fill = `${d} L${toX(shape.points[shape.points.length - 1][0]).toFixed(2)} ${BASE} L${toX(
      shape.points[0][0]
    ).toFixed(2)} ${BASE} Z`;
    body = (
      <>
        <path className="qnt-curve-fill" d={fill} />
        <path className="qnt-curve-line" d={d} />
      </>
    );
  } else if (shape?.kind === "bars") {
    const peak = Math.max(...shape.bars.map((b) => b.p), 0.0001);
    body = (
      <>
        {shape.bars.map((b) => (
          <rect
            key={b.x}
            className="qnt-curve-bar"
            x={toX(b.x) - 3}
            y={toY(b.p / peak)}
            width={6}
            height={Math.max(BASE - toY(b.p / peak), 1)}
          />
        ))}
      </>
    );
  }

  const elicited =
    summary.widened && summary.elicited_lo !== null && summary.elicited_hi !== null
      ? [summary.elicited_lo, summary.elicited_hi]
      : [];

  return (
    <div className="qnt-preview">
      <svg
        className="qnt-preview-svg"
        viewBox={`0 0 ${W} ${H}`}
        width="100%"
        role="img"
        aria-label={`Shape of the ${summary.kind} distribution, mean ${formatValue(
          summary.mean
        )} ${unit}`}
      >
        <line className="qnt-axis" x1={PAD_X} y1={BASE} x2={W - PAD_X} y2={BASE} />
        {body}
        {elicited.map((v) => (
          <line
            key={v}
            className="qnt-tick-elicited"
            x1={toX(v)}
            y1={TOP}
            x2={toX(v)}
            y2={BASE}
          />
        ))}
        <line className="qnt-tick-mean" x1={toX(summary.mean)} y1={TOP} x2={toX(summary.mean)} y2={BASE} />
        <text className="qnt-axis-label" x={PAD_X} y={H - 4}>
          {formatValue(summary.lo)}
        </text>
        <text className="qnt-axis-label qnt-axis-end" x={W - PAD_X} y={H - 4}>
          {formatValue(summary.hi)}
        </text>
      </svg>

      <dl className="qnt-stats">
        <div>
          <dt>Mean</dt>
          <dd>
            {formatValue(summary.mean)} {unit}
          </dd>
        </div>
        <div>
          <dt>Std dev</dt>
          <dd>
            {formatValue(summary.sd)} {unit}
          </dd>
        </div>
        <div>
          <dt title="Conditional mean scaled by the occurrence probability. A sanity check against the simulated mean, never a contingency on its own.">
            Expected
          </dt>
          <dd>
            {formatValue(summary.expected_value)} {unit}
          </dd>
        </div>
      </dl>

      {summary.widened && summary.elicited_lo !== null && summary.elicited_hi !== null && (
        <p className="qnt-widened">
          Bounds widened from {formatValue(summary.elicited_lo)}–
          {formatValue(summary.elicited_hi)} to {formatValue(summary.lo)}–
          {formatValue(summary.hi)}. Your figures are the percentiles, marked on the chart;
          the tails beyond them are what contingency has to cover.
        </p>
      )}
    </div>
  );
}
