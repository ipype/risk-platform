import { useCallback, useEffect, useMemo, useState } from "react";
import QuantPanel from "../components/quant/QuantPanel";
import { getQuantCoverage, getQuantVocabulary, getTriage, setTriage } from "../quant/api";
import { getRisks } from "../api";
import type { QuantCoverage, QuantScenario, QuantVocabulary } from "../quant/types";
import type { Risk } from "../types";
import "../quant.css";

/**
 * Where a register becomes something a simulation can read.
 *
 * Two panes: what still needs eliciting, and the estimate itself. The coverage line at the
 * top is the reason for the split — a run over a register where a third of the flagged
 * risks were never elicited produces a clean, confident, and far too low contingency, and
 * nothing in the output says so. The gap has to be visible before the run, not after.
 *
 * Presented as "Risk Scoring". The file and route keep the `quantify` name because that is
 * what the API path and the coverage endpoint are called, and renaming a URL to match a tab
 * label breaks every bookmark to gain nothing.
 */

export default function QuantifyView() {
  const [vocabulary, setVocabulary] = useState<QuantVocabulary | null>(null);
  const [risks, setRisks] = useState<Risk[]>([]);
  const [flagged, setFlagged] = useState<Set<number>>(new Set());
  const [coverage, setCoverage] = useState<QuantCoverage | null>(null);
  // Coverage is reported for the pre-mitigation pass: that is the set a baseline run
  // reads, and the post-mitigation gap only means anything once mitigations are costed.
  const scenario: QuantScenario = "pre_mitigation";
  const [selected, setSelected] = useState<number | null>(null);
  const [flaggedOnly, setFlaggedOnly] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refreshCoverage = useCallback(async () => {
    try {
      const [triage, cov] = await Promise.all([getTriage(), getQuantCoverage(scenario)]);
      setFlagged(new Set(triage.risk_ids));
      setCoverage(cov);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load coverage");
    }
  }, [scenario]);

  useEffect(() => {
    (async () => {
      try {
        const [vocab, riskRows] = await Promise.all([
          getQuantVocabulary(),
          getRisks({ limit: 500 }),
        ]);
        setVocabulary(vocab);
        setRisks(riskRows);
        await refreshCoverage();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not load the risk scoring view");
      } finally {
        setLoading(false);
      }
    })();
  }, [refreshCoverage]);

  const missing = useMemo(() => new Set(coverage?.missing ?? []), [coverage]);

  const visible = useMemo(
    () => (flaggedOnly ? risks.filter((r) => flagged.has(r.id)) : risks),
    [risks, flagged, flaggedOnly]
  );

  async function toggleFlag(riskId: number, on: boolean) {
    // Optimistic: the checkbox is the analyst's own action and a round trip on every tick
    // makes bulk triage feel broken. The refresh underneath corrects any drift.
    setFlagged((prev) => {
      const next = new Set(prev);
      if (on) next.add(riskId);
      else next.delete(riskId);
      return next;
    });
    try {
      await setTriage([riskId], on);
      await refreshCoverage();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update the flag");
      await refreshCoverage();
    }
  }

  if (loading) return <div className="qnt-view qnt-empty">Loading…</div>;
  if (!vocabulary) return <div className="qnt-view qnt-empty">{error ?? "Unavailable"}</div>;

  const chosen = risks.find((r) => r.id === selected) ?? null;

  return (
    <div className="qnt-view">
      <aside className="qnt-rail">
        <header className="qnt-rail-head">
          <h2 className="qnt-rail-title">Risk Scoring</h2>
          {coverage && (
            <p className="qnt-coverage">
              <strong>
                {coverage.estimated}/{coverage.flagged_for_quantification}
              </strong>{" "}
              flagged risks elicited
              {coverage.missing.length > 0 && (
                <span className="qnt-gap"> · {coverage.missing.length} still missing</span>
              )}
            </p>
          )}
          <label className="qnt-check">
            <input
              type="checkbox"
              checked={flaggedOnly}
              onChange={(e) => setFlaggedOnly(e.target.checked)}
            />
            <span>Flagged only</span>
          </label>
        </header>

        {error && <p className="qnt-banner">{error}</p>}

        <ul className="qnt-risklist">
          {visible.map((risk) => (
            <li key={risk.id}>
              <div
                className={
                  selected === risk.id ? "qnt-riskrow qnt-riskrow-active" : "qnt-riskrow"
                }
              >
                <input
                  type="checkbox"
                  checked={flagged.has(risk.id)}
                  aria-label={`Flag ${risk.risk_code} for scoring`}
                  onChange={(e) => void toggleFlag(risk.id, e.target.checked)}
                />
                <button
                  type="button"
                  className="qnt-riskbtn"
                  onClick={() => setSelected(risk.id)}
                >
                  <span className="qnt-code">{risk.risk_code}</span>
                  <span className="qnt-risktitle">{risk.title}</span>
                </button>
                {flagged.has(risk.id) && missing.has(risk.id) && (
                  <span className="qnt-pill" title="Flagged but not yet elicited">
                    none
                  </span>
                )}
              </div>
            </li>
          ))}
          {visible.length === 0 && (
            <li className="qnt-empty-note">
              {flaggedOnly
                ? "Nothing flagged yet. Untick the filter and flag the risks worth scoring — usually everything at or above your matrix threshold."
                : "No risks in the register."}
            </li>
          )}
        </ul>
      </aside>

      <main className="qnt-main">
        {chosen ? (
          <QuantPanel
            key={chosen.id}
            riskId={chosen.id}
            riskCode={chosen.risk_code}
            riskTitle={chosen.title}
            vocabulary={vocabulary}
            onSaved={() => void refreshCoverage()}
          />
        ) : (
          <div className="qnt-empty">
            <p>Pick a risk to elicit its cost and schedule impact.</p>
            <p className="qnt-muted">
              The matrix decides which risks land here. It never supplies the numbers.
            </p>
          </div>
        )}
      </main>
    </div>
  );
}
