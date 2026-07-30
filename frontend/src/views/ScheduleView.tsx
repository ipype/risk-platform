import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import DcmaReportPanel from "../components/schedule/DcmaReportPanel";
import {
  getDcma,
  getScheduleFormats,
  getScheduleVersions,
  parseStoredFile,
  uploadSchedule,
} from "../api";
import type {
  AmbiguousProjectChoice,
  DcmaRun,
  ScheduleFormat,
  ScheduleUploadResult,
  ScheduleVersionSummary,
} from "../types";
import "../schedule.css";

/** Mirrors ``MAX_UPLOAD_BYTES`` in ``schedule_ingest.py``. */
const MAX_BYTES = 64 * 1024 * 1024;

function suffixOf(name: string): string {
  const i = name.lastIndexOf(".");
  return i === -1 ? "" : name.slice(i).toLowerCase();
}

function sizeMb(bytes: number): string {
  return `${(bytes / 1_048_576).toFixed(1)} MB`;
}

export default function ScheduleView() {
  const [formats, setFormats] = useState<ScheduleFormat[]>([]);
  const [versions, setVersions] = useState<ScheduleVersionSummary[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [dcma, setDcma] = useState<DcmaRun | null>(null);
  const [dcmaMissing, setDcmaMissing] = useState(false);

  const [choice, setChoice] = useState<AmbiguousProjectChoice | null>(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const inputRef = useRef<HTMLInputElement>(null);

  const fail = useCallback((e: unknown) => {
    setError(e instanceof Error ? e.message : String(e));
  }, []);

  const acceptAttr = useMemo(
    () =>
      formats
        .filter((f) => f.available)
        .flatMap((f) => f.suffixes)
        .join(","),
    [formats]
  );

  const refreshVersions = useCallback(async (selectId?: number) => {
    const v = await getScheduleVersions();
    setVersions(v);
    setSelectedId((prev) => selectId ?? prev ?? v.find((x) => x.is_current)?.id ?? v[0]?.id ?? null);
  }, []);

  useEffect(() => {
    let live = true;
    Promise.all([getScheduleFormats(), getScheduleVersions()])
      .then(([f, v]) => {
        if (!live) return;
        setFormats(f);
        setVersions(v);
        setSelectedId(v.find((x) => x.is_current)?.id ?? v[0]?.id ?? null);
      })
      .catch(fail)
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [fail]);

  useEffect(() => {
    if (selectedId == null) {
      setDcma(null);
      return;
    }
    let live = true;
    setDcmaMissing(false);
    getDcma(selectedId)
      .then((r) => live && setDcma(r))
      .catch((e) => {
        if (!live) return;
        // A version with no gate run is a state to report, not an error to shout about.
        setDcma(null);
        if (e instanceof Error && e.message.startsWith("404")) setDcmaMissing(true);
        else fail(e);
      });
    return () => {
      live = false;
    };
  }, [selectedId, fail]);

  const settle = useCallback(
    async (result: ScheduleUploadResult) => {
      setChoice(null);
      await refreshVersions(result.version.id);
      setSelectedId(result.version.id);
      setNotice(
        `${result.version.project_name || result.version.source_project_id}: ` +
          `${result.version.activity_count} activities, ${result.version.relationship_count} links. ` +
          (result.gate.gate_passed
            ? "DCMA gate passed."
            : `DCMA gate blocked on check ${result.gate.blocking_failures.join(", ")}.`) +
          (result.file_created ? "" : " These exact bytes were already stored, so no new file was created.")
      );
    },
    [refreshVersions]
  );

  const send = useCallback(
    async (file: File) => {
      setError(null);
      setNotice(null);

      // Check locally what the server would reject anyway. A 64 MB round trip to be told
      // the format is unreadable is a bad trade when the answer is already on screen.
      const suffix = suffixOf(file.name);
      const known = formats.find((f) => f.suffixes.includes(suffix));
      if (!known) {
        setError(
          `${suffix || file.name} is not a schedule format this platform reads. Supported: ${
            formats.flatMap((f) => f.suffixes).join(", ") || "none registered"
          }.`
        );
        return;
      }
      if (!known.available) {
        setError(`${known.name} cannot be read here. ${known.reason}`);
        return;
      }
      if (file.size === 0) {
        setError("That file is empty.");
        return;
      }
      if (file.size > MAX_BYTES) {
        setError(`That file is ${sizeMb(file.size)}; the limit is ${MAX_BYTES / 1_048_576} MB.`);
        return;
      }

      setUploading(true);
      try {
        const outcome = await uploadSchedule(file);
        if (outcome.kind === "ambiguous") {
          setChoice(outcome.choice);
          setNotice(null);
        } else {
          await settle(outcome.result);
        }
      } catch (e) {
        fail(e);
      } finally {
        setUploading(false);
      }
    },
    [formats, settle, fail]
  );

  const pickProject = useCallback(
    async (projectId: string) => {
      if (!choice) return;
      setUploading(true);
      setError(null);
      try {
        await settle(await parseStoredFile(choice.file_id, projectId));
      } catch (e) {
        fail(e);
      } finally {
        setUploading(false);
      }
    },
    [choice, settle, fail]
  );

  const selected = versions.find((v) => v.id === selectedId) ?? null;

  return (
    <div className="sch">
      <header className="topbar">
        <h1>Schedule</h1>
      </header>

      <div
        className={dragging ? "sch-drop is-dragging" : "sch-drop"}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          const f = e.dataTransfer.files?.[0];
          if (f && !uploading) void send(f);
        }}
      >
        <input
          ref={inputRef}
          id="sch-file"
          type="file"
          className="sch-file"
          accept={acceptAttr || undefined}
          disabled={uploading}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void send(f);
            // Reset so re-picking the same file after a failure still fires a change.
            e.target.value = "";
          }}
        />
        <label htmlFor="sch-file" className="sch-drop-cta">
          {uploading ? "Parsing and running the gate…" : "Choose a schedule file"}
        </label>
        <p className="sch-drop-hint">
          or drop it here. Every import is parsed and put through the DCMA 14-point gate
          immediately — the result decides whether it can be simulated against.
        </p>
        <ul className="sch-formats">
          {formats.map((f) => (
            <li key={f.suffixes.join(",")} className={f.available ? "is-ok" : "is-off"}>
              <b>{f.suffixes.join(" ")}</b> {f.name}
              {!f.available && <em title={f.reason}> — {f.reason}</em>}
            </li>
          ))}
        </ul>
      </div>

      <div role="status" aria-live="polite" className="sch-messages">
        {error && <p className="sch-error">{error}</p>}
        {notice && <p className="sch-notice">{notice}</p>}
      </div>

      {choice && (
        <section className="sch-choice">
          <h2>This export holds {choice.projects.length} projects</h2>
          <p className="sch-check-note">
            The file is stored already, so picking one finishes the job without re-uploading.
          </p>
          <ul>
            {choice.projects.map((p) => (
              <li key={p.id}>
                <button type="button" disabled={uploading} onClick={() => void pickProject(p.id)}>
                  <b>{p.name || p.id}</b>
                  <span>{p.activity_count} activities</span>
                </button>
              </li>
            ))}
          </ul>
          <button type="button" className="sch-link" onClick={() => setChoice(null)}>
            Cancel
          </button>
        </section>
      )}

      {loading ? (
        <p className="sch-empty">Loading…</p>
      ) : versions.length === 0 ? (
        <p className="sch-empty">
          No schedule imported yet. Once one is in, risks can be mapped to activities and the
          gate result will show here.
        </p>
      ) : (
        <div className="sch-body">
          <section className="sch-versions">
            <h2>Versions</h2>
            <ul>
              {versions.map((v) => (
                <li key={v.id}>
                  <button
                    type="button"
                    className={v.id === selectedId ? "sch-version is-selected" : "sch-version"}
                    aria-current={v.id === selectedId}
                    onClick={() => setSelectedId(v.id)}
                  >
                    <span className="sch-version-name">
                      {v.project_name || v.source_project_id}
                      {v.is_current && <em className="sch-badge-current">current</em>}
                    </span>
                    <span className="sch-version-meta">
                      {v.source_format} · {v.activity_count} activities ·{" "}
                      {new Date(v.created_at).toLocaleDateString()}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>

          <div className="sch-detail">
            {selected && (
              <>
                <dl className="sch-facts">
                  <div>
                    <dt>Data date</dt>
                    <dd>
                      {selected.data_date
                        ? new Date(selected.data_date).toLocaleDateString()
                        : "—"}
                    </dd>
                  </div>
                  <div>
                    <dt>Must finish by</dt>
                    <dd>
                      {selected.must_finish_by
                        ? new Date(selected.must_finish_by).toLocaleDateString()
                        : "—"}
                    </dd>
                  </div>
                  <div>
                    <dt>Relationships</dt>
                    <dd>{selected.relationship_count}</dd>
                  </div>
                  <div>
                    <dt>Parsed by</dt>
                    <dd>
                      {selected.created_by} · {selected.parser_version}
                    </dd>
                  </div>
                </dl>

                {selected.warnings.length > 0 && (
                  <div className="sch-warnings">
                    <b>Parser warnings</b>
                    <ul>
                      {selected.warnings.map((w) => (
                        <li key={w}>{w}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </>
            )}

            {dcma ? (
              <DcmaReportPanel run={dcma} />
            ) : dcmaMissing ? (
              <p className="sch-empty">No gate run recorded for this version yet.</p>
            ) : (
              <p className="sch-empty">Loading gate result…</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
