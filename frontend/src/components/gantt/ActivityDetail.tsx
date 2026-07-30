/**
 * What is true about the selected activity, and why its bar sits where it does.
 *
 * The predecessor and successor lists are the reason there are no dependency arrows on
 * the chart. Across thousands of windowed rows an arrow to an off-screen row is a line
 * you cannot follow; a named list you can click to jump to answers the same question and
 * carries the relationship type and lag, which an arrow never does.
 */

import { useEffect, useState } from "react";
import { getRelationshipsTouching } from "../../api";
import type { ActivityLanding, GanttBar, ScheduleRelationship } from "../../types";
import { BASIS_NOTE, fmtDate, fmtDays, fmtMoney, fmtPct, humanize } from "./gantt-util";

interface Props {
  versionId: number;
  bar: GanttBar;
  wbsPath: string;
  landing: ActivityLanding | undefined;
  landingsUnavailable: boolean;
  barsById: Map<string, GanttBar>;
  onJump: (sourceId: string) => void;
  onClose: () => void;
}

const VIA_LABEL: Record<string, string> = {
  direct: "drives this activity",
  scope: "via a scoped driver",
  insert_predecessor: "inserted work after this",
  insert_successor: "inserted work before this",
};

function Field({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="gt-field">
      <dt>{label}</dt>
      <dd>
        {value}
        {hint && <span className="gt-field-hint">{hint}</span>}
      </dd>
    </div>
  );
}

export default function ActivityDetail({
  versionId,
  bar,
  wbsPath,
  landing,
  landingsUnavailable,
  barsById,
  onJump,
  onClose,
}: Props) {
  const [links, setLinks] = useState<ScheduleRelationship[] | null>(null);
  const [linkError, setLinkError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setLinks(null);
    setLinkError(null);
    getRelationshipsTouching(versionId, bar.source_id)
      .then((page) => live && setLinks(page.items))
      .catch((e) => live && setLinkError(String(e)));
    return () => {
      live = false;
    };
  }, [versionId, bar.source_id]);

  const predecessors = (links ?? []).filter((l) => l.successor_source_id === bar.source_id);
  const successors = (links ?? []).filter((l) => l.predecessor_source_id === bar.source_id);

  const slip = bar.baseline_slip_calendar_days;
  const floatHint =
    bar.total_float_days !== null && bar.total_float_days < 0
      ? "negative — the schedule cannot meet its constraint"
      : undefined;

  function LinkList({
    title,
    items,
    otherEnd,
  }: {
    title: string;
    items: ScheduleRelationship[];
    otherEnd: (l: ScheduleRelationship) => string;
  }) {
    if (items.length === 0) return <p className="muted gt-detail-none">No {title.toLowerCase()}.</p>;
    return (
      <ul className="gt-links">
        {items.map((link) => {
          const id = otherEnd(link);
          const other = barsById.get(id);
          return (
            <li key={link.id}>
              <button
                type="button"
                className="gt-link-jump"
                onClick={() => onJump(id)}
                disabled={!other}
                title={
                  other
                    ? "Show this activity on the chart"
                    : "Outside the rows currently loaded — widen the filter to reach it"
                }
              >
                <span className="gt-link-code">{other ? other.code : id}</span>
                <span className="gt-link-name">
                  {other ? other.name : "not in the loaded rows"}
                </span>
              </button>
              <span className="gt-link-meta">
                {link.type}
                {link.lag_days ? ` ${link.lag_days > 0 ? "+" : ""}${link.lag_days}d` : ""}
                {link.lag_days && link.lag_calendar_id ? ` on ${link.lag_calendar_id}` : ""}
              </span>
            </li>
          );
        })}
      </ul>
    );
  }

  return (
    <aside className="gt-detail" aria-label={`Activity ${bar.code}`}>
      <header className="gt-detail-head">
        <div>
          <span className="gt-detail-code">{bar.code}</span>
          <h2>{bar.name || "(unnamed)"}</h2>
          {wbsPath && <p className="gt-detail-path">{wbsPath}</p>}
        </div>
        <button type="button" className="gt-detail-close" onClick={onClose} aria-label="Close">
          ×
        </button>
      </header>

      <div className="gt-detail-tags">
        <span className="gt-tag">{humanize(bar.type)}</span>
        <span className="gt-tag">{humanize(bar.status)}</span>
        {bar.is_critical && <span className="gt-tag gt-tag--critical">critical</span>}
        {bar.has_hard_constraint && (
          <span className="gt-tag gt-tag--warn">{humanize(bar.constraint_type)}</span>
        )}
      </div>

      <p className="gt-detail-basis">{BASIS_NOTE[bar.basis]}</p>

      <section>
        <h3>Dates</h3>
        <dl className="gt-fields">
          <Field label="Start" value={fmtDate(bar.start)} />
          <Field label="Finish" value={fmtDate(bar.finish)} />
          <Field label="Baseline start" value={fmtDate(bar.baseline_start)} />
          <Field label="Baseline finish" value={fmtDate(bar.baseline_finish)} />
          {slip !== null && (
            <Field
              label="Slip vs baseline"
              value={`${slip > 0 ? "+" : ""}${slip} calendar days`}
              hint={slip > 0 ? "later than baseline" : slip < 0 ? "ahead of baseline" : undefined}
            />
          )}
        </dl>
      </section>

      <section>
        <h3>Duration and float</h3>
        <dl className="gt-fields">
          <Field
            label="Original"
            value={fmtDays(bar.original_duration_days, bar.duration_calendar_id)}
          />
          <Field
            label="Remaining"
            value={fmtDays(bar.remaining_duration_days, bar.duration_calendar_id)}
          />
          <Field
            label="Total float"
            value={fmtDays(bar.total_float_days, bar.duration_calendar_id)}
            hint={floatHint}
          />
          <Field
            label="Duration complete"
            value={fmtPct(bar.duration_pct_complete)}
            hint="remaining against original, not a physical percent"
          />
          <Field label="Budgeted cost" value={fmtMoney(bar.budgeted_cost)} />
        </dl>
      </section>

      <section>
        <h3>
          Risks landed
          {landing && (
            <span className="gt-detail-counts">
              {landing.accepted} accepted · {landing.proposed} proposed
            </span>
          )}
        </h3>
        {landingsUnavailable ? (
          <p className="muted gt-detail-none">Risk overlay unavailable — see the banner above.</p>
        ) : !landing || landing.risks.length === 0 ? (
          <p className="muted gt-detail-none">
            Nothing points at this activity. On driving work that is a gap, not a clean bill.
          </p>
        ) : (
          <>
            <ul className="gt-landed">
              {landing.risks.map((risk) => (
                <li key={risk.mapping_id} className={`gt-landed--${risk.status}`}>
                  <span className="gt-landed-code">{risk.risk_code ?? `risk ${risk.risk_id}`}</span>
                  <span className="gt-landed-title">{risk.title ?? ""}</span>
                  <span className="gt-landed-meta">
                    {risk.status} · {VIA_LABEL[risk.via] ?? risk.via}
                  </span>
                </li>
              ))}
            </ul>
            {landing.risks_truncated > 0 && (
              <p className="muted gt-detail-none">
                and {landing.risks_truncated} more — open the mapping tab for the full list.
              </p>
            )}
          </>
        )}
      </section>

      <section>
        <h3>Logic</h3>
        {linkError ? (
          <p className="error">{linkError}</p>
        ) : links === null ? (
          <p className="muted gt-detail-none">Loading…</p>
        ) : (
          <>
            <h4>Predecessors</h4>
            <LinkList
              title="Predecessors"
              items={predecessors}
              otherEnd={(l) => l.predecessor_source_id}
            />
            <h4>Successors</h4>
            <LinkList
              title="Successors"
              items={successors}
              otherEnd={(l) => l.successor_source_id}
            />
          </>
        )}
      </section>
    </aside>
  );
}
