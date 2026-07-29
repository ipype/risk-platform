import { useState } from "react";
import type { Mapping } from "../../types";

const TYPE_LABEL: Record<string, string> = {
  duration_driver: "drives duration",
  inserted_activity: "inserts activity",
  scoped_driver: "scoped driver",
};

export interface MappingRowProps {
  mapping: Mapping;
  busy: boolean;
  onUpdate: (id: number, patch: Partial<Mapping>) => void;
  onDelete: (id: number) => void;
}

export default function MappingRow({ mapping, busy, onUpdate, onDelete }: MappingRowProps) {
  const [rationale, setRationale] = useState(mapping.rationale ?? "");
  const [allocation, setAllocation] = useState(
    mapping.allocation_pct == null ? "" : String(mapping.allocation_pct)
  );

  const warnings = mapping.warnings ?? [];
  const errors = warnings.filter((w) => w.startsWith("error:"));
  const advisories = warnings.filter((w) => !w.startsWith("error:"));

  const target =
    mapping.mapping_type === "scoped_driver"
      ? `${mapping.scope?.field} ${mapping.scope?.op} ${mapping.scope?.value}`
      : mapping.mapping_type === "inserted_activity"
        ? `${mapping.predecessor_name ?? mapping.predecessor_source_id} → ${
            mapping.successor_name ?? mapping.successor_source_id
          }`
        : (mapping.activity_name ?? mapping.activity_source_id ?? "");

  const rationaleDirty = rationale !== (mapping.rationale ?? "");
  const allocationDirty =
    allocation !== (mapping.allocation_pct == null ? "" : String(mapping.allocation_pct));

  return (
    <div className="map-mapping">
      <div className="map-mapping-head">
        <span className={`map-chip map-chip-${mapping.status}`}>{mapping.status}</span>
        <span className="map-mapping-type">{TYPE_LABEL[mapping.mapping_type]}</span>
        {mapping.origin === "carried_forward" && (
          <span className="map-hint" title="Carried from an earlier schedule version — confirm it still holds">
            carried forward
          </span>
        )}
      </div>

      <div className="map-card-name" title={target} style={{ marginTop: 4 }}>
        {mapping.activity_code ? `${mapping.activity_code} · ` : ""}
        {target}
      </div>

      {mapping.mapping_type === "scoped_driver" && (
        <div className="map-hint" style={{ marginTop: 3 }}>
          matches {mapping.resolved_count ?? 0} activities right now
        </div>
      )}
      {mapping.mapping_type === "inserted_activity" && mapping.existing_link === false && (
        <div className="map-hint" style={{ marginTop: 3 }}>
          no existing link between these two
        </div>
      )}
      {mapping.materiality && (
        <div className="map-card-meta">
          <span
            className={`map-chip map-chip-${mapping.materiality.band}`}
            title={mapping.materiality.why}
          >
            {mapping.materiality.band === "high"
              ? "critical path"
              : `${mapping.materiality.band} impact`}
          </span>
          {mapping.suggestion_score != null && (
            <span className="map-hint">
              suggested at {Math.round(mapping.suggestion_score * 100)}%
            </span>
          )}
        </div>
      )}

      {errors.length > 0 && (
        <div className="map-warn is-error">
          <ul>
            {errors.map((w) => (
              <li key={w}>{w.replace(/^error:\s*/, "")}</li>
            ))}
          </ul>
        </div>
      )}
      {advisories.length > 0 && (
        <div className="map-warn">
          <ul>
            {advisories.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Allocation only exists for inserted activities. Driver mappings share one
          sampled factor across every activity they drive, which is what makes them
          correlated — the API refuses an allocation on them, so no input is offered. */}
      {mapping.mapping_type === "inserted_activity" && (
        <label className="map-field">
          <span style={{ whiteSpace: "nowrap" }}>Share %</span>
          <input
            type="number"
            min={0}
            max={100}
            value={allocation}
            disabled={busy}
            onChange={(e) => setAllocation(e.target.value)}
            onBlur={() => {
              if (!allocationDirty) return;
              onUpdate(mapping.id, {
                allocation_pct: allocation === "" ? null : Number(allocation),
              });
            }}
          />
        </label>
      )}

      <label className="map-field" style={{ alignItems: "flex-start" }}>
        <span className="map-sr-only">Rationale</span>
        <textarea
          placeholder="Why this activity…"
          value={rationale}
          disabled={busy}
          onChange={(e) => setRationale(e.target.value)}
          onBlur={() => rationaleDirty && onUpdate(mapping.id, { rationale })}
        />
      </label>

      <div className="map-card-actions">
        {mapping.status !== "accepted" && (
          <button
            type="button"
            className="map-btn map-btn-primary"
            disabled={busy || errors.length > 0}
            onClick={() => onUpdate(mapping.id, { status: "accepted" })}
            title={errors.length > 0 ? errors[0] : "Accept — this becomes visible to simulation"}
          >
            Accept
          </button>
        )}
        {mapping.status === "accepted" && (
          <button
            type="button"
            className="map-btn"
            disabled={busy}
            onClick={() => onUpdate(mapping.id, { status: "proposed" })}
          >
            Un-accept
          </button>
        )}
        <button
          type="button"
          className="map-btn map-btn-danger"
          disabled={busy}
          onClick={() => onDelete(mapping.id)}
        >
          Remove
        </button>
      </div>

      <div className="map-hint" style={{ marginTop: 4 }}>
        {mapping.decided_by
          ? `${mapping.status} by ${mapping.decided_by}`
          : `proposed by ${mapping.proposed_by}`}
      </div>
    </div>
  );
}
