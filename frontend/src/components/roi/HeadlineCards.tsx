import type { Comparison, SeriesReduction } from "../../roi-types";
import { fmtMoney, fmtPercent, fmtUnits } from "../sim/format";

/**
 * The four numbers a reviewer actually asks for, and the one they should not be given.
 *
 * The headline is the contingency reduction, and it is shown with its error bar attached
 * rather than beside it: a reduction that does not clear the sampling error is not a small
 * result, it is not a result, and the card says so in place of the figure's confidence
 * rather than in a footnote nobody reaches.
 *
 * Package cost sits in its own card with a rule between it and the contingency cards. The
 * layout is doing work here — the two are different kinds of money (deterministic and
 * additive versus a percentile of a distribution), and a screen that lined them up in one
 * row would invite exactly the addition invariant 1 forbids.
 */

interface Props {
  comparison: Comparison;
}

function Figure({ value, units }: { value: number | null; units: string }) {
  return <span className="roi-figure">{fmtUnits(value, units)}</span>;
}

function ReductionCard({
  title,
  series,
  hint,
}: {
  title: string;
  series: SeriesReduction | null;
  hint?: string;
}) {
  if (series === null || series.at_percentile.reduction === null) {
    return (
      <div className="roi-card muted">
        <h4>{title}</h4>
        <p className="roi-empty">Not reported by these runs.</p>
      </div>
    );
  }

  const cut = series.at_percentile.reduction;
  const worse = cut < 0;

  return (
    <div className={worse ? "roi-card worse" : "roi-card"}>
      <h4>{title}</h4>
      <div className="roi-beforeafter">
        <div>
          <span className="roi-label">Baseline</span>
          <Figure value={series.at_percentile.before} units={series.units} />
        </div>
        <span className="roi-arrow" aria-hidden="true">
          →
        </span>
        <div>
          <span className="roi-label">After</span>
          <Figure value={series.at_percentile.after} units={series.units} />
        </div>
      </div>
      <p className={worse ? "roi-headline worse" : "roi-headline"}>
        {worse ? "Increase of " : "Reduction of "}
        {fmtUnits(Math.abs(cut), series.units)}
        {series.at_percentile.reduction_pct !== null ? (
          <span className="roi-pct"> ({fmtPercent(Math.abs(series.at_percentile.reduction_pct))})</span>
        ) : null}
      </p>
      {series.within_noise ? (
        <p className="roi-note warn">
          Smaller than the estimated sampling error
          {series.standard_error !== null ? ` (±${fmtUnits(series.standard_error, series.units)})` : ""}.
          These two runs are not distinguishable at this iteration count.
        </p>
      ) : series.standard_error !== null ? (
        <p className="roi-note">
          Estimated error on the difference: ±{fmtUnits(series.standard_error, series.units)} (upper
          bound).
        </p>
      ) : null}
      {hint !== undefined ? <p className="roi-note">{hint}</p> : null}
    </div>
  );
}

export function HeadlineCards({ comparison }: Props) {
  const p = comparison.percentile;
  return (
    <div className="roi-cards">
      <ReductionCard title={`Contingency at P${p}`} series={comparison.contingency} />
      <ReductionCard title={`Total cost at P${p}`} series={comparison.total_cost} />
      {comparison.delay_days !== null ? (
        <ReductionCard title={`Delay at P${p}`} series={comparison.delay_days} />
      ) : null}

      <div className="roi-card cost">
        <h4>What the package costs</h4>
        <div className="roi-costrow">
          <span className="roi-label">Budget</span>
          <span className="roi-figure">{fmtMoney(comparison.plan_budget)}</span>
        </div>
        <div className="roi-costrow">
          <span className="roi-label">Programme consumed</span>
          <span className="roi-figure">{comparison.plan_sched_days.toFixed(1)} d</span>
        </div>
        <p className="roi-note">
          Deterministic and additive. Contingency is a percentile and is not. These are
          never added into one number.
        </p>
        {comparison.benefit_cost_ratio !== null ? (
          <div className="roi-costrow strong">
            <span className="roi-label">Contingency removed per unit spent</span>
            <span className="roi-figure">{comparison.benefit_cost_ratio.toFixed(2)}×</span>
          </div>
        ) : (
          <p className="roi-note">
            No priced action in this package, so there is no ratio to report.
          </p>
        )}
        {comparison.net_at_percentile !== null ? (
          <div className="roi-costrow">
            <span className="roi-label">Net at P{p}</span>
            <span className="roi-figure">{fmtMoney(comparison.net_at_percentile)}</span>
          </div>
        ) : null}
        {comparison.plan_unpriced_count > 0 ? (
          <p className="roi-note warn">
            {comparison.plan_unpriced_count} action(s) carry neither a budget nor a duration,
            so the cost side is understated.
          </p>
        ) : null}
      </div>
    </div>
  );
}
