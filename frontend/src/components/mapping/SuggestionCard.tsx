import type { MappingCandidate, SignalName } from "../../types";

/** Human labels for the four relevance signals, in the order they are shown. */
const SIGNAL_LABELS: [SignalName, string, string][] = [
  ["lexical", "Wording", "Shared vocabulary, weighted by how rare each word is in this schedule"],
  ["taxonomy", "Category", "Words the RBS category expects to see on a matching activity"],
  ["wbs_affinity", "WBS", "Sits in a branch this risk is already mapped into"],
  ["precedent", "Precedent", "How this subcategory has been mapped before, on accepted and rejected suggestions alike"],
];

function pct(v: number): string {
  return `${Math.round(v * 100)}%`;
}

export interface SuggestionCardProps {
  candidate: MappingCandidate;
  focused: boolean;
  busy: boolean;
  onAccept: (c: MappingCandidate) => void;
  onReject: (c: MappingCandidate) => void;
  onFocus: (c: MappingCandidate) => void;
}

export default function SuggestionCard({
  candidate,
  focused,
  busy,
  onAccept,
  onReject,
  onFocus,
}: SuggestionCardProps) {
  const errors = candidate.warnings.filter((w) => w.startsWith("error:"));
  const advisories = candidate.warnings.filter((w) => !w.startsWith("error:"));
  const blocked = errors.length > 0;

  return (
    <div
      className={focused ? "map-card is-focused" : "map-card"}
      onMouseEnter={() => onFocus(candidate)}
    >
      <div className="map-card-head">
        <span className="map-riskcode">{candidate.activity_code}</span>
        <span className="map-card-name" title={candidate.activity_name}>
          {candidate.activity_name}
        </span>
      </div>

      {candidate.wbs_path && (
        <div className="map-card-wbs" title={candidate.wbs_path}>
          {candidate.wbs_path}
        </div>
      )}

      <div className="map-card-meta">
        <span className={`map-chip map-chip-${candidate.confidence}`}>
          {candidate.confidence} · {pct(candidate.score)}
        </span>
        {/* Materiality is deliberately its own chip. It answers "does delay here move
            the finish date", which is a different question from "is this the right
            activity", and merging them would just rank the critical path highest. */}
        <span
          className={`map-chip map-chip-${candidate.materiality.band}`}
          title={candidate.materiality.why}
        >
          {candidate.materiality.band === "high" ? "critical path" : `${candidate.materiality.band} impact`}
        </span>
        {candidate.recommended_type !== "duration_driver" && (
          <span className="map-hint">
            suggest as {candidate.recommended_type.replace(/_/g, " ")}
          </span>
        )}
        {candidate.remaining_duration_days != null && (
          <span className="map-hint">{candidate.remaining_duration_days}d remaining</span>
        )}
      </div>

      <div className="map-signals">
        {SIGNAL_LABELS.map(([key, label, help]) => {
          const value = candidate.signals[key];
          return (
            <div key={key} style={{ display: "contents" }}>
              <span className="map-signal-name" title={help}>
                {label}
              </span>
              {value == null ? (
                <span className="map-signal-abstain">no evidence yet</span>
              ) : (
                <>
                  <span className="map-signal-track">
                    <span
                      className="map-signal-fill"
                      style={{ width: `${Math.max(2, value * 100)}%` }}
                    />
                  </span>
                  <span className="map-signal-value">{pct(value)}</span>
                </>
              )}
            </div>
          );
        })}
      </div>

      {candidate.matched_terms.length > 0 && (
        <div className="map-terms">
          matched:{" "}
          {candidate.matched_terms.slice(0, 8).map((t) => (
            <span className="map-term" key={t}>
              {t}
            </span>
          ))}
        </div>
      )}

      {blocked && (
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

      <div className="map-card-actions">
        <button
          type="button"
          className="map-btn map-btn-primary"
          disabled={busy || blocked}
          onClick={() => onAccept(candidate)}
          title={blocked ? errors[0] : "Accept as a mapping"}
        >
          Accept
        </button>
        <button
          type="button"
          className="map-btn"
          disabled={busy}
          onClick={() => onReject(candidate)}
          title="Dismiss. Recorded so the ranking learns from it."
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}
