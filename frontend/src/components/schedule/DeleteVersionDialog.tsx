/**
 * Confirming the removal of an imported schedule.
 *
 * The dialog exists to state a quantity, not to ask a question. "This cannot be undone"
 * on its own asks the analyst to accept a cost nobody has measured for them, so every
 * number here comes from `GET /schedules/{id}/delete-impact` — counted server-side,
 * before anything is touched.
 *
 * Two levels of friction, matched to what is actually at stake. Activities, links, WBS
 * and gate runs are all reproducible from the stored bytes, so losing them is a re-import
 * and a single button is right. Accepted risk-to-activity mappings are analyst judgement
 * and are not recoverable from any file, so those need an explicit acknowledgement — and
 * the server refuses without one regardless of what this component sends.
 */

import { useEffect, useRef, useState } from "react";
import type { ScheduleDeleteImpact } from "../../types";

interface Props {
  impact: ScheduleDeleteImpact;
  busy: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: (opts: { force: boolean; deleteFile: boolean }) => void;
}

function sizeMb(bytes: number): string {
  return `${(bytes / 1_048_576).toFixed(1)} MB`;
}

export default function DeleteVersionDialog({
  impact,
  busy,
  error,
  onCancel,
  onConfirm,
}: Props) {
  const [acknowledged, setAcknowledged] = useState(false);
  const [deleteFile, setDeleteFile] = useState(false);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  // Focus lands on Cancel, not on Delete. A dialog that opens with the destructive
  // action under the return key is a trap for anyone confirming by keyboard.
  useEffect(() => {
    cancelRef.current?.focus();
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape" && !busy) onCancel();
      if (event.key !== "Tab") return;
      const focusable = panelRef.current?.querySelectorAll<HTMLElement>(
        "button:not([disabled]), input:not([disabled])"
      );
      if (!focusable || focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [busy, onCancel]);

  const blocked = impact.needs_force && !acknowledged;

  return (
    <div className="sch-modal" role="presentation" onMouseDown={() => !busy && onCancel()}>
      <div
        className="sch-modal-panel"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="sch-del-title"
        aria-describedby="sch-del-body"
        ref={panelRef}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <h2 id="sch-del-title">
          Delete “{impact.project_name || impact.source_project_id}”?
        </h2>

        <div id="sch-del-body">
          <p className="sch-modal-lead">
            Version #{impact.version_id}
            {impact.is_current && " — the current version of this project"}. The parse
            below is removed. The analysis is reproducible from the source file; anything
            under “Risk mapping” is not.
          </p>

          <dl className="sch-modal-counts">
            <div>
              <dt>Activities</dt>
              <dd>{impact.activities.toLocaleString()}</dd>
            </div>
            <div>
              <dt>Dependencies</dt>
              <dd>{impact.relationships.toLocaleString()}</dd>
            </div>
            <div>
              <dt>WBS nodes</dt>
              <dd>{impact.wbs_nodes.toLocaleString()}</dd>
            </div>
            <div>
              <dt>Calendars</dt>
              <dd>{impact.calendars.toLocaleString()}</dd>
            </div>
            <div>
              <dt>DCMA runs</dt>
              <dd>{impact.dcma_runs.toLocaleString()}</dd>
            </div>
            <div>
              <dt>Risk mappings</dt>
              <dd>{impact.mappings_total.toLocaleString()}</dd>
            </div>
          </dl>

          {impact.needs_force ? (
            <div className="sch-modal-warn">
              <b>
                {impact.mappings_accepted} accepted risk-to-activity mapping
                {impact.mappings_accepted === 1 ? "" : "s"} will be deleted.
              </b>{" "}
              An accepted mapping is a decision about where a risk lands on the network.
              Re-importing the file will not bring it back — it would have to be made
              again. The change history for each one is kept.
            </div>
          ) : impact.mappings_proposed > 0 ? (
            <p className="sch-modal-note">
              {impact.mappings_proposed} proposed mapping
              {impact.mappings_proposed === 1 ? "" : "s"} will go too. Nothing has been
              accepted against this version, so no decision is lost.
            </p>
          ) : null}

          {impact.is_current &&
            (impact.promotes_version_id !== null ? (
              <p className="sch-modal-note">
                Version #{impact.promotes_version_id} becomes the current version of this
                project.
              </p>
            ) : (
              <p className="sch-modal-note">
                This is the only version of this project. Deleting it leaves nothing for
                risks to be mapped against until a schedule is imported again.
              </p>
            ))}

          <label className={`sch-modal-check${impact.file_removable ? "" : " is-off"}`}>
            <input
              type="checkbox"
              checked={deleteFile && impact.file_removable}
              disabled={!impact.file_removable || busy}
              onChange={(e) => setDeleteFile(e.target.checked)}
            />
            <span>
              Also delete the stored file — <code>{impact.filename}</code>,{" "}
              {sizeMb(impact.file_size_bytes)}
              {!impact.file_removable && (
                <em>
                  {" "}
                  — kept: {impact.file_versions_remaining} other version
                  {impact.file_versions_remaining === 1 ? " was" : "s were"} parsed from
                  it
                </em>
              )}
            </span>
          </label>

          {impact.needs_force && (
            <label className="sch-modal-check sch-modal-check--ack">
              <input
                type="checkbox"
                checked={acknowledged}
                disabled={busy}
                onChange={(e) => setAcknowledged(e.target.checked)}
              />
              <span>
                I understand {impact.mappings_accepted} accepted mapping
                {impact.mappings_accepted === 1 ? "" : "s"} will be lost.
              </span>
            </label>
          )}

          {error && (
            <p className="sch-error" role="alert">
              {error}
            </p>
          )}
        </div>

        <div className="sch-modal-actions">
          <button type="button" ref={cancelRef} disabled={busy} onClick={onCancel}>
            Cancel
          </button>
          <button
            type="button"
            className="sch-danger"
            disabled={busy || blocked}
            onClick={() =>
              onConfirm({
                force: impact.needs_force,
                deleteFile: deleteFile && impact.file_removable,
              })
            }
          >
            {busy ? "Deleting…" : "Delete version"}
          </button>
        </div>
      </div>
    </div>
  );
}
