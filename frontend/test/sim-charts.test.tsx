/**
 * What the simulation charts actually draw.
 *
 * Assertions are on rendered markup because that is what the reader sees. Where a check
 * looks brittle — matching on a class name, on a formatted string — that is the point: the
 * class carries the colour that says cost from schedule, and the formatted string carries
 * the currency symbol. Both have been wrong before and neither is visible to `tsc`.
 *
 * Fixtures are synthetic rather than a captured run. A captured run would drift from the
 * engine and would have to be re-captured on every version bump; these shapes only have to
 * satisfy the types, and the engine's own suite is what proves the numbers.
 */

import { renderToStaticMarkup } from "react-dom/server";
import type { ReactElement } from "react";
import DistributionChart from "../src/components/sim/DistributionChart";
import Tornado from "../src/components/sim/Tornado";
import type { RiskSensitivity, SeriesSummary } from "../src/simulation-types";

const N = 10_000;

function series(label: string, units: string, lo: number, hi: number): SeriesSummary {
  return {
    label,
    units,
    iterations: N,
    mean: (lo + hi) / 2,
    sd: (hi - lo) / 6,
    minimum: lo,
    maximum: hi,
    percentiles: [50, 80].map((p) => ({ p, value: lo + ((hi - lo) * p) / 100 })),
    s_curve: Array.from({ length: 101 }, (_, i) => ({
      x: lo + ((hi - lo) * i) / 100,
      p: i / 100,
    })),
    histogram: {
      edges: Array.from({ length: 51 }, (_, i) => lo + ((hi - lo) * i) / 50),
      counts: Array.from({ length: 50 }, (_, i) =>
        Math.round(200 * Math.exp(-((i - 20) ** 2) / 80))
      ),
    },
  };
}

const cost = series("Total cost", "currency", 25_000_000, 31_000_000);
const delay = series("Schedule delay", "days", 0, 90);

/** Every iteration identical: the case that divides by zero if the span is not floored. */
const degenerate: SeriesSummary = {
  ...series("Flat", "currency", 5, 5),
  s_curve: Array.from({ length: 101 }, (_, i) => ({ x: 5, p: i / 100 })),
  histogram: { edges: [5, 6], counts: [N] },
};

/** A run that produced nothing drawable — the engine refuses this, the chart should too. */
const empty: SeriesSummary = { ...cost, s_curve: [], histogram: { edges: [], counts: [] } };

const rows: RiskSensitivity[] = [
  {
    risk_id: 1,
    code: "EXT-WEA-0001",
    title: "Winter shutdown",
    cost_variance_share: 0.31,
    schedule_variance_share: 0.12,
    combined_variance_share: 0.43,
    delay_variance_share: 0.22,
    spearman_total_cost: 0.6,
    spearman_delay: 0.5,
    mean_contribution: 240_000,
    p80_contribution: 700_000,
    realised_frequency: 0.4,
  },
  {
    risk_id: 2,
    code: "CON-PRD-0002",
    title: "Productivity shortfall",
    cost_variance_share: 0.18,
    schedule_variance_share: 0.05,
    combined_variance_share: 0.23,
    delay_variance_share: 0.09,
    spearman_total_cost: 0.4,
    spearman_delay: 0.3,
    mean_contribution: 180_000,
    p80_contribution: 500_000,
    realised_frequency: 0.6,
  },
  {
    // An opportunity: negative share, drives no activity. Both nullable paths at once.
    risk_id: 3,
    code: "COM-ESC-0003",
    title: "Steel escalation",
    cost_variance_share: -0.04,
    schedule_variance_share: null,
    combined_variance_share: -0.04,
    delay_variance_share: null,
    spearman_total_cost: -0.1,
    spearman_delay: null,
    mean_contribution: -20_000,
    p80_contribution: 100_000,
    realised_frequency: 1.0,
  },
];

/** A run stored before engine 1.2.0: the field is absent, not null. */
const legacy: RiskSensitivity[] = rows.map(({ delay_variance_share: _drop, ...rest }) => rest);

const costOnly: RiskSensitivity[] = [rows[2]];

export default function main(): { lines: string[]; failed: number } {
  const lines: string[] = [];
  let failed = 0;
  const check = (name: string, cond: boolean): void => {
    if (cond) {
      lines.push(`ok   ${name}`);
    } else {
      failed += 1;
      lines.push(`FAIL ${name}`);
    }
  };
  const r = (el: ReactElement): string => renderToStaticMarkup(el);

  /* --- currency, which the platform prints without owning a currency field ---------- */

  const cdf = r(<DistributionChart series={cost} defaultMarkers={[50, 80, 95]} accent="cost" />);
  check("cost readout carries a currency symbol", /<dd>\$[\d,]+<\/dd>/.test(cdf));
  check("cost axis ticks carry a symbol", cdf.includes(">$"));

  const delayCdf = r(<DistributionChart series={delay} defaultMarkers={[50, 80]} accent="sched" />);
  check(
    "a delay chart is in days and never in currency",
    /<dd>[\d.]+ d<\/dd>/.test(delayCdf) && !delayCdf.includes("$")
  );

  /* --- percentile tagging ----------------------------------------------------------- */

  check("every requested marker renders", (cdf.match(/>P(50|80|95)</g) ?? []).length >= 6);
  check("the readout lists one value per marker", (cdf.match(/<dd>/g) ?? []).length === 3);
  check("preset percentiles are offered", cdf.includes(">P10<") && cdf.includes(">P90<"));
  check("a free percentile can be typed", cdf.includes('class="sim-p-input"'));
  check(
    "a percentile outside the requested set is interpolated, not dropped",
    r(<DistributionChart series={cost} defaultMarkers={[73.5]} />).includes(">P73.5<")
  );
  check(
    "no markers renders no readout strip",
    !r(<DistributionChart series={cost} defaultMarkers={[]} />).includes("sim-marker-readout")
  );

  /* --- the three views -------------------------------------------------------------- */

  check(
    "the cumulative curve is what opens",
    cdf.includes("sim-curve-line") && !cdf.includes("sim-pdf-bar")
  );
  const pdf = r(<DistributionChart series={cost} defaultMode="pdf" accent="cost" />);
  check("density draws bars and no curve", pdf.includes("sim-pdf-bar") && !pdf.includes("sim-curve-line"));
  const both = r(<DistributionChart series={cost} defaultMode="both" accent="cost" />);
  check("both draws bars and curve", both.includes("sim-pdf-bar") && both.includes("sim-curve-line"));
  check("both says which axis belongs to which", both.includes("Left axis is cumulative probability"));
  check(
    "the schedule colour reaches the bars",
    r(<DistributionChart series={delay} defaultMode="pdf" accent="sched" />).includes("sim-pdf-bar sched")
  );

  /* --- edges ------------------------------------------------------------------------ */

  check("a degenerate series still draws", r(<DistributionChart series={degenerate} />).includes("<svg"));
  check(
    "an empty series refuses instead of dividing by zero",
    r(<DistributionChart series={empty} />).includes("Not enough points")
  );

  /* --- tornado: three readings ------------------------------------------------------ */

  const tCost = r(<Tornado rows={rows} metric="cost" />);
  check("the cost view keeps every risk", tCost.includes("EXT-WEA-0001") && tCost.includes("COM-ESC-0003"));
  check("a negative share gets its own colour", tCost.includes("sim-bar-cost negative"));
  check("the cost view does not split bars", !tCost.includes("sim-bar-sched"));

  const tSched = r(<Tornado rows={rows} metric="schedule" />);
  check("the schedule view drops what drives no activity", !tSched.includes("COM-ESC-0003"));
  check("the schedule view prints how much the register explains", tSched.includes("31%"));
  check("the schedule view uses the schedule colour", tSched.includes("sim-bar-sched"));

  const barelyDrives = rows.map((x) => ({
    ...x,
    delay_variance_share: x.delay_variance_share == null ? null : 0.05,
  }));
  check(
    "a register that barely drives the date is told so outright",
    r(<Tornado rows={barelyDrives} metric="schedule" />).includes("will not move the finish much")
  );

  const tBoth = r(<Tornado rows={rows} metric="combined" />);
  check("both together splits the bars", tBoth.includes("sim-bar-cost") && tBoth.includes("sim-bar-sched"));
  check(
    "both together keeps the engine's ordering rather than re-sorting",
    tBoth.indexOf("EXT-WEA-0001") < tBoth.indexOf("CON-PRD-0002")
  );

  /* --- tornado: runs the field predates --------------------------------------------- */

  const tLegacy = r(<Tornado rows={legacy} metric="schedule" />);
  check("an older run falls back to rank correlation", tLegacy.includes("predates the delay variance share"));
  check("the fallback prints coefficients, not percentages", tLegacy.includes(">0.50<"));
  check(
    "a register with nothing mapped says so rather than drawing an empty chart",
    r(<Tornado rows={costOnly} metric="schedule" />).includes("No risk is mapped")
  );

  /* --- the tooltip bug this runner was written to catch ------------------------------ */

  check(
    "a tornado tooltip is one text node, not markup the browser will show as text",
    /<title>EXT-WEA-0001 —[^<]*mean cost contribution \$240,000<\/title>/.test(tBoth)
  );

  return { lines, failed };
}
