import { useEffect, useState } from "react";
import type { BandDef, MatrixConfig, Risk } from "../types";
import { getMatrixConfig, getRisks } from "../api";

const OVERALL = "__overall__";

function bandColor(score: number, bands: BandDef[]): string {
  const b = bands.find((x) => score >= x.min_score && score <= x.max_score);
  return b ? b.color : "transparent";
}

export default function MatrixView() {
  const [config, setConfig] = useState<MatrixConfig | null>(null);
  const [risks, setRisks] = useState<Risk[]>([]);
  const [lens, setLens] = useState<string>(OVERALL);
  const [selected, setSelected] = useState<{ p: number; i: number } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([getMatrixConfig(), getRisks()])
      .then(([cfg, rs]) => {
        setConfig(cfg);
        setRisks(rs);
      })
      .catch((e) => setError(String(e)));
  }, []);

  if (error) return <div className="error">{error}</div>;
  if (!config) return <div className="muted">Loading…</div>;

  function lensImpact(r: Risk): number | null {
    if (lens === OVERALL) return r.impact;
    const v = r.impact_scores?.[lens];
    return typeof v === "number" ? v : null;
  }

  const probRows = [...config.probability_levels].sort((a, b) => b.level - a.level);
  const impCols = [...config.impact_levels].sort((a, b) => a.level - b.level);

  function risksInCell(p: number, i: number): Risk[] {
    return risks.filter((r) => r.probability === p && lensImpact(r) === i);
  }

  const placed = risks.filter(
    (r) => r.probability !== null && lensImpact(r) !== null
  ).length;
  const unscored = risks.length - placed;

  const cellRisks = selected ? risksInCell(selected.p, selected.i) : [];

  return (
    <div className="matrixview">
      <header className="topbar">
        <h1>Risk matrix</h1>
        <label className="lens">
          View by
          <select value={lens} onChange={(e) => { setLens(e.target.value); setSelected(null); }}>
            <option value={OVERALL}>Overall (worst area)</option>
            {config.impact_areas.map((a) => (
              <option key={a.code} value={a.code}>
                {a.name}
              </option>
            ))}
          </select>
        </label>
      </header>

      <table className="matrix">
        <tbody>
          {probRows.map((p) => (
            <tr key={p.level}>
              <th className="rowhead">
                {p.level} · {p.label}
              </th>
              {impCols.map((im) => {
                const list = risksInCell(p.level, im.level);
                const isSel = selected?.p === p.level && selected?.i === im.level;
                return (
                  <td key={im.level} className="cellwrap">
                    <button
                      className={`cell ${isSel ? "sel" : ""}`}
                      style={{ background: bandColor(p.level * im.level, config.bands) }}
                      onClick={() => setSelected({ p: p.level, i: im.level })}
                      aria-label={`Probability ${p.label}, impact ${im.label}, ${list.length} risks`}
                    >
                      {list.length > 0 ? list.length : ""}
                    </button>
                  </td>
                );
              })}
            </tr>
          ))}
          <tr>
            <th className="rowhead"></th>
            {impCols.map((im) => (
              <th key={im.level} className="colhead">
                {im.level} · {im.label}
              </th>
            ))}
          </tr>
        </tbody>
      </table>

      <div className="legend">
        {config.bands.map((b) => (
          <span key={b.name} className="legend-item">
            <span className="swatch" style={{ background: b.color }} />
            {b.name}
          </span>
        ))}
        <span className="legend-count">
          {placed} of {risks.length} risks placed
          {unscored > 0 ? ` · ${unscored} not scored on this view` : ""}
        </span>
      </div>

      {selected && (
        <div className="cell-risks">
          <h3>
            {cellRisks.length} risk{cellRisks.length === 1 ? "" : "s"} at probability{" "}
            {selected.p}, impact {selected.i}
          </h3>
          {cellRisks.length === 0 ? (
            <p className="muted">No risks in this cell.</p>
          ) : (
            <ul>
              {cellRisks.map((r) => (
                <li key={r.id}>
                  <strong>{r.risk_code}</strong> — {r.title}
                  {r.risk_level ? <span className="tag"> {r.risk_level}</span> : null}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
