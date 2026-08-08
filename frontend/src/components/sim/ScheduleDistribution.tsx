import { useState } from "react";
import type { SeriesSummary } from "../../simulation-types";
import DistributionChart from "./DistributionChart";
import { fmtDays } from "./format";

/**
 * The schedule answer, read as a slip or as a finish.
 *
 * Two different questions get asked of the same run and neither is a rephrasing of the
 * other. "How much are we going to be late" is a slip against the plan and is what a risk
 * register is scored on. "When does this actually finish" is a duration or a date, and is
 * the only one of the two anybody outside the room can act on — a contract has a date in
 * it, not a delay allowance.
 *
 * The engine already produces both series, so this is a switch rather than arithmetic:
 * `delay_days` is measured from the deterministic finish, `finish_day` from day zero of
 * the network. Deriving one from the other in the UI would mean shifting a histogram and
 * an S-curve by a constant and hoping the constant was the right one.
 *
 * Only the finish offers a calendar reading. A slip of forty days is not a date and
 * rendering it as one would be inventing an origin for it.
 */

type View = "delay" | "finish";

interface Props {
  delay: SeriesSummary;
  /** Absent only on a run made before the engine reported a finish series. */
  finish?: SeriesSummary | null;
  /** `YYYY-MM-DD` day zero of the network, when the schedule version carries one. */
  dayZero?: string | null;
  /** This engine's own deterministic finish, in elapsed days from day zero. */
  baselineFinishDay?: number | null;
  idPrefix: string;
}

export default function ScheduleDistribution({
  delay,
  finish,
  dayZero = null,
  baselineFinishDay,
  idPrefix,
}: Props) {
  const [view, setView] = useState<View>("delay");
  // A run that never reported a finish series has nothing to switch to, and must not be
  // left showing a control that does nothing.
  const active: View = finish ? view : "delay";

  return (
    <>
      <div className="sim-jcl-controls" role="group" aria-label="Schedule measure">
        <span className="sim-jcl-controls-label">Measure</span>
        <button
          type="button"
          className={active === "delay" ? "sim-chip active" : "sim-chip"}
          aria-pressed={active === "delay"}
          onClick={() => setView("delay")}
        >
          Slip against plan
        </button>
        {finish && (
          <button
            type="button"
            className={active === "finish" ? "sim-chip active" : "sim-chip"}
            aria-pressed={active === "finish"}
            onClick={() => setView("finish")}
          >
            Project finish
          </button>
        )}
      </div>

      <p className="sim-note">
        In <strong>elapsed days</strong>, not working days. A schedule spanning several
        calendars has no single working week, so durations are converted to elapsed time
        before the network is run and everything below comes back on that axis.
      </p>

      {active === "delay" || !finish ? (
        <>
          <p className="sim-note">
            Measured against this engine's own deterministic forward pass, which finishes
            on day {fmtDays(baselineFinishDay)} — not against the dates in the imported
            schedule, which came out of P6 under constraints and progress overrides this
            pass does not model.
          </p>
          <DistributionChart
            series={delay}
            defaultMarkers={[50, 80]}
            accent="sched"
            idPrefix={`delay-${idPrefix}`}
          />
        </>
      ) : (
        <>
          <p className="sim-note">
            Duration from day zero of the schedule, or the calendar date that lands on.
            {dayZero == null &&
              " No data date was parsed with this schedule and no activity carried an" +
                " early start, so there is no anchor to render dates against — the" +
                " durations below are still exact."}
          </p>
          <DistributionChart
            series={finish}
            defaultMarkers={[50, 80]}
            accent="sched"
            idPrefix={`finish-${idPrefix}`}
            dayZero={dayZero}
            defaultAsDate={dayZero != null}
          />
        </>
      )}
    </>
  );
}
