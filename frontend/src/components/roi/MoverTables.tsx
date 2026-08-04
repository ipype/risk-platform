import type { CriticalityMover, RiskMover } from "../../roi-types";
import { fmtMoney, fmtPercent } from "../sim/format";

/**
 * Which risks the package moved, and which paths it moved onto the critical path.
 *
 * Both tables keep the unflattering rows. A risk whose contribution went *up* and an
 * activity that became *more* critical are the two findings that change what somebody
 * does next, and a screen that ranked only improvements would sort them off the bottom.
 * The risk table is ordered by contribution removed, so an increase sinks to the end
 * where it is still reachable; the criticality table is ordered by absolute movement, so
 * a newly critical path sorts to the top alongside the one that was cleared.
 */

const MOVEMENT_LABEL: Record<RiskMover["movement"], string> = {
  retired: "Retired",
  reduced: "Reduced",
  unchanged: "Unchanged",
  increased: "Increased",
  entered: "New since baseline",
};

function rank(before: number | null, after: number | null): string {
  if (before === null && after === null) return "—";
  if (after === null) return `#${before} → out`;
  if (before === null) return `new → #${after}`;
  return `#${before} → #${after}`;
}

export function RiskMoverTable({ movers }: { movers: RiskMover[] }) {
  if (movers.length === 0) {
    return <p className="roi-empty">Neither run reported per-risk sensitivity.</p>;
  }

  return (
    <div className="roi-tablewrap">
      <table className="roi-table">
        <thead>
          <tr>
            <th scope="col">Risk</th>
            <th scope="col">Treatment</th>
            <th scope="col" className="num">
              Contribution before
            </th>
            <th scope="col" className="num">
              After
            </th>
            <th scope="col" className="num">
              Removed
            </th>
            <th scope="col" className="num">
              Variance share
            </th>
            <th scope="col" className="num">
              Rank
            </th>
          </tr>
        </thead>
        <tbody>
          {movers.map((m) => (
            <tr key={m.risk_id} className={m.movement === "increased" ? "worse" : undefined}>
              <th scope="row">
                <span className="roi-code">{m.code}</span>
                <span className="roi-title">{m.title}</span>
              </th>
              <td>
                <span className={`roi-badge ${m.movement}`}>{MOVEMENT_LABEL[m.movement]}</span>
              </td>
              <td className="num">{fmtMoney(m.contribution_before)}</td>
              <td className="num">{m.movement === "retired" ? "—" : fmtMoney(m.contribution_after)}</td>
              <td className="num strong">{fmtMoney(m.contribution_reduction)}</td>
              <td className="num">
                {fmtPercent(m.share_before)} → {fmtPercent(m.share_after)}
              </td>
              <td className="num">{rank(m.rank_before, m.rank_after)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function CriticalityShiftTable({ movers }: { movers: CriticalityMover[] }) {
  if (movers.length === 0) {
    return (
      <p className="roi-empty">
        No schedule was simulated, so there is no critical path to compare.
      </p>
    );
  }

  const moved = movers.filter((m) => Math.abs(m.index_change ?? 0) > 0.001).slice(0, 20);
  if (moved.length === 0) {
    return <p className="roi-empty">No activity's criticality index moved measurably.</p>;
  }

  return (
    <div className="roi-tablewrap">
      <table className="roi-table">
        <thead>
          <tr>
            <th scope="col">Activity</th>
            <th scope="col" className="num">
              Criticality before
            </th>
            <th scope="col" className="num">
              After
            </th>
            <th scope="col" className="num">
              Change
            </th>
          </tr>
        </thead>
        <tbody>
          {moved.map((m) => {
            const worse = (m.index_change ?? 0) > 0;
            return (
              <tr key={m.activity_id} className={worse ? "worse" : undefined}>
                <th scope="row">
                  <span className="roi-code">{m.code || m.activity_id}</span>
                  <span className="roi-title">{m.name}</span>
                </th>
                <td className="num">{fmtPercent(m.index_before)}</td>
                <td className="num">{fmtPercent(m.index_after)}</td>
                <td className="num strong">
                  {worse ? "+" : ""}
                  {fmtPercent(m.index_change)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="roi-note">
        A rise is not a failure of the package. Taking risk off one path routinely promotes
        another, and the activity that just became critical is the one to look at next.
      </p>
    </div>
  );
}
