import { useState } from "react";
import type { DcmaCheck, DcmaRun } from "../../types";

const STATUS_LABEL: Record<DcmaCheck["status"], string> = {
  pass: "Pass",
  fail: "Fail",
  not_assessed: "Not assessed",
};

/**
 * Failed and blocking first, then failed, then the rest in DCMA order.
 *
 * The published numbering is not a priority order, and a reader scanning fourteen rows
 * for the two that stop the job should not have to.
 */
function byUrgency(a: DcmaCheck, b: DcmaCheck): number {
  const rank = (c: DcmaCheck) =>
    c.status === "fail" ? (c.blocking ? 0 : 1) : c.status === "not_assessed" ? 2 : 3;
  const ra = rank(a);
  const rb = rank(b);
  return ra !== rb ? ra - rb : a.number - b.number;
}

function CheckRow({ check }: { check: DcmaCheck }) {
  const [open, setOpen] = useState(false);
  const hasOffenders = check.offenders.length > 0;

  return (
    <li className={`sch-check is-${check.status}`}>
      <div className="sch-check-main">
        <span className="sch-check-num">{check.number}</span>
        <span className="sch-check-name">
          {check.name}
          {check.blocking && (
            <em className="sch-badge-blocking" title="Failure of this check blocks simulation">
              blocking
            </em>
          )}
        </span>
        <span className={`sch-pill is-${check.status}`}>{STATUS_LABEL[check.status]}</span>
        <span className="sch-check-metric">
          {check.metric_label || "—"}
          {check.threshold_label && <small>vs {check.threshold_label}</small>}
        </span>
        {hasOffenders ? (
          <button
            type="button"
            className="sch-check-toggle"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
          >
            {open ? "Hide" : `${check.offender_count} offending`}
          </button>
        ) : (
          <span className="sch-check-toggle is-empty">
            {check.offender_count > 0 ? `${check.offender_count} offending` : ""}
          </span>
        )}
      </div>

      {check.note && <p className="sch-check-note">{check.note}</p>}

      {open && hasOffenders && (
        <div className="sch-offenders">
          <ul>
            {check.offenders.map((o) => (
              <li key={o}>{o}</li>
            ))}
          </ul>
          {check.truncated && (
            <p className="sch-check-note">
              Showing {check.offenders.length} of {check.offender_count}. Export the schedule
              to work through the rest.
            </p>
          )}
        </div>
      )}
    </li>
  );
}

export default function DcmaReportPanel({ run }: { run: DcmaRun }) {
  const checks = [...run.report.checks].sort(byUrgency);
  const failed = checks.filter((c) => c.status === "fail").length;
  const notAssessed = checks.filter((c) => c.status === "not_assessed").length;
  const passed = checks.length - failed - notAssessed;

  return (
    <section className="sch-dcma">
      <div className={run.gate_passed ? "sch-verdict is-pass" : "sch-verdict is-blocked"}>
        <div>
          <b>{run.gate_passed ? "Gate passed" : "Gate blocked"}</b>
          <span>
            {run.gate_passed
              ? "This schedule is fit to simulate against."
              : `Blocking failures on check ${run.blocking_failures.join(", ")}. Simulation stays closed until these are fixed or a human overrides the gate on the record.`}
          </span>
        </div>
        <div className="sch-verdict-counts">
          <span>
            <b>{passed}</b>
            <small>passed</small>
          </span>
          <span>
            <b>{failed}</b>
            <small>failed</small>
          </span>
          <span>
            <b>{notAssessed}</b>
            <small>not assessed</small>
          </span>
        </div>
      </div>

      <p className="sch-meta">
        Run {run.run_id} by {run.run_by} · {new Date(run.created_at).toLocaleString()} ·{" "}
        {run.report.project_name || run.report.project_id}
      </p>

      <ul className="sch-checks">
        {checks.map((c) => (
          <CheckRow key={c.number} check={c} />
        ))}
      </ul>
    </section>
  );
}
