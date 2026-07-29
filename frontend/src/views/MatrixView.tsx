import { useEffect, useMemo, useState } from "react";
import type { BandDef, Category, MatrixConfig, Risk } from "../types";
import type { MatrixBasis } from "../api";
import { exportMatrix, getCategories, getMatrixConfig, getRisks } from "../api";
import "../matrix.css";

const OVERALL = "__overall__";
const MAX_RISKS = 500;
const STATUSES = ["Open", "Analyzing", "Mitigating", "Closed"];

type CellMode = "count" | "codes";

function bandFor(score: number, bands: BandDef[]): BandDef | undefined {
  return bands.find((b) => score >= b.min_score && score <= b.max_score);
}

/**
 * Where a risk sits for the selected lens and basis. Mirrors
 * `app/services/matrix_export.placement_for` — the two must not drift, or the screen and
 * the exported file will disagree about the same register.
 */
function placementOf(
  risk: Risk,
  lens: string,
  basis: MatrixBasis
): { probability: number | null; impact: number | null } {
  const probability = basis === "target" ? risk.target_probability : risk.probability;
  const scores = (basis === "target" ? risk.target_impact_scores : risk.impact_scores) ?? {};
  const overall = basis === "target" ? risk.target_impact : risk.impact;
  if (lens === OVERALL) return { probability, impact: overall };
  const raw = scores[lens];
  return { probability, impact: typeof raw === "number" ? raw : null };
}

export default function MatrixView() {
  const [config, setConfig] = useState<MatrixConfig | null>(null);
  const [categories, setCategories] = useState<Category[]>([]);
  const [risks, setRisks] = useState<Risk[]>([]);

  const [lens, setLens] = useState<string>(OVERALL);
  const [basis, setBasis] = useState<MatrixBasis>("current");
  const [category, setCategory] = useState<string>("");
  const [status, setStatus] = useState<string>("");
  const [owner, setOwner] = useState<string>("");
  const [cellMode, setCellMode] = useState<CellMode>("count");

  const [selected, setSelected] = useState<{ p: number; i: number } | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    Promise.all([getMatrixConfig(), getCategories()])
      .then(([cfg, cats]) => {
        if (!live) return;
        setConfig(cfg);
        setCategories(cats);
      })
      .catch((e) => live && setError(String(e)));
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    let live = true;
    setRefreshing(true);
    getRisks({ category: category || undefined, status: status || undefined, limit: MAX_RISKS })
      .then((rs) => {
        if (!live) return;
        setRisks(rs);
        setError(null);
      })
      .catch((e) => live && setError(String(e)))
      .finally(() => {
        if (!live) return;
        setLoading(false);
        setRefreshing(false);
      });
    return () => {
      live = false;
    };
  }, [category, status]);

  const visible = useMemo(() => {
    const needle = owner.trim().toLowerCase();
    if (!needle) return risks;
    return risks.filter((r) => (r.owner ?? "").toLowerCase().includes(needle));
  }, [risks, owner]);

  const { cells, unplaced } = useMemo(() => {
    const map = new Map<string, Risk[]>();
    const missed: Risk[] = [];
    const probs = new Set((config?.probability_levels ?? []).map((l) => l.level));
    const imps = new Set((config?.impact_levels ?? []).map((l) => l.level));
    for (const risk of visible) {
      const { probability, impact } = placementOf(risk, lens, basis);
      if (probability === null || impact === null || !probs.has(probability) || !imps.has(impact)) {
        missed.push(risk);
        continue;
      }
      const key = `${probability}:${impact}`;
      const list = map.get(key);
      if (list) list.push(risk);
      else map.set(key, [risk]);
    }
    for (const list of map.values()) list.sort((a, b) => a.risk_code.localeCompare(b.risk_code));
    missed.sort((a, b) => a.risk_code.localeCompare(b.risk_code));
    return { cells: map, unplaced: missed };
  }, [visible, lens, basis, config]);

  if (error && !config) return <div className="error">{error}</div>;
  if (!config) return <div className="muted">Loading…</div>;

  const probRows = [...config.probability_levels].sort((a, b) => b.level - a.level);
  const impCols = [...config.impact_levels].sort((a, b) => a.level - b.level);
  const placedCount = visible.length - unplaced.length;
  const cellRisks = selected ? (cells.get(`${selected.p}:${selected.i}`) ?? []) : [];
  const filtersOn = Boolean(category || status || owner.trim());
  const lensName =
    lens === OVERALL
      ? "Overall (worst area)"
      : (config.impact_areas.find((a) => a.code === lens)?.name ?? lens);

  function resetView() {
    setCategory("");
    setStatus("");
    setOwner("");
    setSelected(null);
  }

  async function runExport(format: "xlsx" | "svg") {
    setExporting(format);
    setError(null);
    try {
      await exportMatrix(
        {
          lens,
          basis,
          category: category || undefined,
          status: status || undefined,
          owner: owner.trim() || undefined,
          showCodes: cellMode === "codes",
          title: `Risk matrix — ${lensName}`,
        },
        format
      );
    } catch (e) {
      setError(String(e));
    } finally {
      setExporting(null);
    }
  }

  return (
    <div className="matrixview">
      <header className="topbar">
        <h1>Risk matrix</h1>
        <span className="muted">
          {placedCount} of {visible.length} placed
          {refreshing ? " · updating…" : ""}
        </span>
      </header>

      {error && <div className="error mx-banner">{error}</div>}

      <div className="mx-toolbar">
        <label className="lens">
          View by
          <select
            value={lens}
            onChange={(e) => {
              setLens(e.target.value);
              setSelected(null);
            }}
          >
            <option value={OVERALL}>Overall (worst area)</option>
            {config.impact_areas.map((a) => (
              <option key={a.code} value={a.code}>
                {a.name}
              </option>
            ))}
          </select>
        </label>

        <div className="mx-segmented" role="group" aria-label="Scoring basis">
          <button
            type="button"
            className={basis === "current" ? "mx-seg on" : "mx-seg"}
            aria-pressed={basis === "current"}
            onClick={() => {
              setBasis("current");
              setSelected(null);
            }}
          >
            Current
          </button>
          <button
            type="button"
            className={basis === "target" ? "mx-seg on" : "mx-seg"}
            aria-pressed={basis === "target"}
            onClick={() => {
              setBasis("target");
              setSelected(null);
            }}
          >
            Residual
          </button>
        </div>

        <label className="lens">
          Category
          <select
            value={category}
            onChange={(e) => {
              setCategory(e.target.value);
              setSelected(null);
            }}
          >
            <option value="">All</option>
            {categories.map((c) => (
              <option key={c.code} value={c.code}>
                {c.name}
              </option>
            ))}
          </select>
        </label>

        <label className="lens">
          Status
          <select
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setSelected(null);
            }}
          >
            <option value="">All</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>

        <label className="lens">
          Owner
          <input
            value={owner}
            placeholder="any"
            onChange={(e) => {
              setOwner(e.target.value);
              setSelected(null);
            }}
          />
        </label>

        <div className="mx-segmented" role="group" aria-label="Cell contents">
          <button
            type="button"
            className={cellMode === "count" ? "mx-seg on" : "mx-seg"}
            aria-pressed={cellMode === "count"}
            onClick={() => setCellMode("count")}
          >
            Counts
          </button>
          <button
            type="button"
            className={cellMode === "codes" ? "mx-seg on" : "mx-seg"}
            aria-pressed={cellMode === "codes"}
            onClick={() => setCellMode("codes")}
          >
            Codes
          </button>
        </div>

        <div className="mx-spacer" />

        {filtersOn && (
          <button type="button" className="mx-link" onClick={resetView}>
            Clear filters
          </button>
        )}
        <button
          type="button"
          className="mx-btn"
          disabled={exporting !== null}
          onClick={() => runExport("xlsx")}
        >
          {exporting === "xlsx" ? "Exporting…" : "Export Excel"}
        </button>
        <button
          type="button"
          className="mx-btn"
          disabled={exporting !== null}
          onClick={() => runExport("svg")}
        >
          {exporting === "svg" ? "Exporting…" : "Export image"}
        </button>
      </div>

      <div className="mx-scroll">
        <table className="matrix">
          <caption className="mx-caption">
            {lensName} · {basis === "target" ? "Residual (post-mitigation)" : "Current (pre-mitigation)"}
          </caption>
          <tbody>
            {probRows.map((p) => (
              <tr key={p.level}>
                <th scope="row" className="rowhead">
                  {p.level} · {p.label}
                </th>
                {impCols.map((im) => {
                  const list = cells.get(`${p.level}:${im.level}`) ?? [];
                  const isSel = selected?.p === p.level && selected?.i === im.level;
                  const band = bandFor(p.level * im.level, config.bands);
                  return (
                    <td key={im.level} className="cellwrap">
                      <button
                        type="button"
                        className={`cell ${isSel ? "sel" : ""} ${
                          cellMode === "codes" ? "mx-cell-codes" : ""
                        }`}
                        style={{ background: band?.color ?? "transparent" }}
                        onClick={() =>
                          setSelected(
                            isSel ? null : { p: p.level, i: im.level }
                          )
                        }
                        aria-pressed={isSel}
                        aria-label={`Probability ${p.label}, impact ${im.label}, ${list.length} risks`}
                      >
                        {list.length === 0 ? (
                          ""
                        ) : cellMode === "count" ? (
                          list.length
                        ) : (
                          <>
                            <span className="mx-cell-count">{list.length}</span>
                            <span className="mx-cell-list">
                              {list.slice(0, 4).map((r) => (
                                <span key={r.id}>{r.risk_code}</span>
                              ))}
                              {list.length > 4 && <span>+{list.length - 4} more</span>}
                            </span>
                          </>
                        )}
                      </button>
                    </td>
                  );
                })}
              </tr>
            ))}
            <tr>
              <th className="rowhead" />
              {impCols.map((im) => (
                <th key={im.level} scope="col" className="colhead">
                  {im.level} · {im.label}
                </th>
              ))}
            </tr>
          </tbody>
        </table>
      </div>

      <div className="legend">
        {config.bands.map((b) => (
          <span key={b.name} className="legend-item">
            <span className="swatch" style={{ background: b.color }} />
            {b.name}
          </span>
        ))}
        <span className="legend-count">
          {placedCount} of {visible.length} risks placed
          {unplaced.length > 0 ? ` · ${unplaced.length} not scored on this view` : ""}
        </span>
      </div>

      {!loading && visible.length === 0 && (
        <p className="muted mx-empty">
          {filtersOn
            ? "No risks match these filters."
            : "No risks in the register yet. Add one from the Register tab."}
        </p>
      )}

      {risks.length === MAX_RISKS && (
        <p className="mx-warn">
          Showing the first {MAX_RISKS} risks. Narrow the filters to be sure the matrix covers
          the whole register.
        </p>
      )}

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
                  {r.owner ? <span className="muted"> · {r.owner}</span> : null}
                  {(basis === "target" ? r.target_risk_level : r.risk_level) ? (
                    <span className="tag">
                      {basis === "target" ? r.target_risk_level : r.risk_level}
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {unplaced.length > 0 && (
        <details className="mx-unplaced">
          <summary>
            {unplaced.length} risk{unplaced.length === 1 ? "" : "s"} not on this matrix
          </summary>
          <p className="muted">
            Not scored for {lensName} on the {basis === "target" ? "residual" : "current"} basis,
            or scored against levels the active scheme no longer defines.
          </p>
          <ul>
            {unplaced.map((r) => (
              <li key={r.id}>
                <strong>{r.risk_code}</strong> — {r.title}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
