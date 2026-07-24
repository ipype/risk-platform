import { useEffect, useState } from "react";
import type { BandDef, LevelDef, MatrixConfig } from "../types";
import { getMatrixConfig, saveMatrixConfig } from "../api";

interface WorkingArea {
  code: string;
  name: string;
  descriptors: string[]; // aligned to impact_levels order
}
interface Working {
  name: string;
  probability_levels: LevelDef[];
  impact_levels: LevelDef[];
  impact_areas: WorkingArea[];
  bands: BandDef[];
}

function renumber(levels: LevelDef[]): LevelDef[] {
  return levels.map((l, i) => ({ ...l, level: i + 1 }));
}

function toWorking(cfg: MatrixConfig): Working {
  return {
    name: cfg.name,
    probability_levels: cfg.probability_levels,
    impact_levels: cfg.impact_levels,
    bands: cfg.bands,
    impact_areas: cfg.impact_areas.map((a) => ({
      code: a.code,
      name: a.name,
      descriptors: cfg.impact_levels.map(
        (l) => a.descriptors[String(l.level)] ?? ""
      ),
    })),
  };
}

function fromWorking(w: Working): MatrixConfig {
  return {
    name: w.name,
    probability_levels: w.probability_levels,
    impact_levels: w.impact_levels,
    bands: w.bands,
    impact_areas: w.impact_areas.map((a) => ({
      code: a.code,
      name: a.name,
      descriptors: Object.fromEntries(
        w.impact_levels.map((l, idx) => [String(l.level), a.descriptors[idx] ?? ""])
      ),
    })),
  };
}

function bandColor(score: number, bands: BandDef[]): string {
  const b = bands.find((x) => score >= x.min_score && score <= x.max_score);
  return b ? b.color : "transparent";
}

export default function MatrixSettings() {
  const [w, setW] = useState<Working | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getMatrixConfig()
      .then((cfg) => setW(toWorking(cfg)))
      .catch((e) => setError(String(e)));
  }, []);

  if (error) return <div className="error">{error}</div>;
  if (!w) return <div className="muted">Loading…</div>;

  const cur = w;
  function patch(p: Partial<Working>) {
    setW({ ...cur, ...p });
    setMessage(null);
  }

  function setProbLabel(i: number, label: string) {
    patch({
      probability_levels: cur.probability_levels.map((l, idx) =>
        idx === i ? { ...l, label } : l
      ),
    });
  }
  function addProbLevel() {
    patch({
      probability_levels: renumber([
        ...cur.probability_levels,
        { level: 0, label: `Level ${cur.probability_levels.length + 1}` },
      ]),
    });
  }
  function removeProbLevel(i: number) {
    if (cur.probability_levels.length <= 2) return;
    patch({
      probability_levels: renumber(
        cur.probability_levels.filter((_, idx) => idx !== i)
      ),
    });
  }

  function setImpLabel(i: number, label: string) {
    patch({
      impact_levels: cur.impact_levels.map((l, idx) =>
        idx === i ? { ...l, label } : l
      ),
    });
  }
  function addImpLevel() {
    patch({
      impact_levels: renumber([
        ...cur.impact_levels,
        { level: 0, label: `Level ${cur.impact_levels.length + 1}` },
      ]),
      impact_areas: cur.impact_areas.map((a) => ({
        ...a,
        descriptors: [...a.descriptors, ""],
      })),
    });
  }
  function removeImpLevel(i: number) {
    if (cur.impact_levels.length <= 2) return;
    patch({
      impact_levels: renumber(cur.impact_levels.filter((_, idx) => idx !== i)),
      impact_areas: cur.impact_areas.map((a) => ({
        ...a,
        descriptors: a.descriptors.filter((_, idx) => idx !== i),
      })),
    });
  }

  function setArea(i: number, field: "code" | "name", value: string) {
    patch({
      impact_areas: cur.impact_areas.map((a, idx) =>
        idx === i ? { ...a, [field]: value } : a
      ),
    });
  }
  function setDescriptor(areaIdx: number, levelIdx: number, value: string) {
    patch({
      impact_areas: cur.impact_areas.map((a, idx) =>
        idx === areaIdx
          ? {
              ...a,
              descriptors: a.descriptors.map((d, di) =>
                di === levelIdx ? value : d
              ),
            }
          : a
      ),
    });
  }
  function addArea() {
    patch({
      impact_areas: [
        ...cur.impact_areas,
        {
          code: "NEW",
          name: "New area",
          descriptors: cur.impact_levels.map(() => ""),
        },
      ],
    });
  }
  function removeArea(i: number) {
    patch({ impact_areas: cur.impact_areas.filter((_, idx) => idx !== i) });
  }

  function setBand(i: number, field: keyof BandDef, value: string) {
    patch({
      bands: cur.bands.map((b, idx) =>
        idx === i
          ? {
              ...b,
              [field]:
                field === "min_score" || field === "max_score"
                  ? Number(value) || 0
                  : value,
            }
          : b
      ),
    });
  }
  function addBand() {
    patch({
      bands: [
        ...cur.bands,
        { name: "New", min_score: 0, max_score: 0, color: "#cccccc" },
      ],
    });
  }
  function removeBand(i: number) {
    if (cur.bands.length <= 1) return;
    patch({ bands: cur.bands.filter((_, idx) => idx !== i) });
  }

  async function handleSave() {
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      await saveMatrixConfig(fromWorking(cur));
      setMessage("Saved. Existing risks have been re-scored against the new scheme.");
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  // preview: probability high->low (rows), impact low->high (cols)
  const probRows = [...cur.probability_levels].sort((a, b) => b.level - a.level);
  const impCols = [...cur.impact_levels].sort((a, b) => a.level - b.level);

  return (
    <div className="settings">
      <header className="topbar">
        <h1>Matrix settings</h1>
        <button className="btn primary" disabled={saving} onClick={handleSave}>
          {saving ? "Saving…" : "Save scheme"}
        </button>
      </header>

      {error && <div className="error">{error}</div>}
      {message && <div className="success">{message}</div>}

      <section className="section">
        <h3>Scheme name</h3>
        <input
          value={cur.name}
          onChange={(e) => patch({ name: e.target.value })}
          style={{ maxWidth: 320 }}
        />
      </section>

      <section className="section">
        <h3>Preview</h3>
        <table className="preview">
          <tbody>
            {probRows.map((p) => (
              <tr key={p.level}>
                <th className="rowhead">{p.level} · {p.label}</th>
                {impCols.map((im) => {
                  const score = p.level * im.level;
                  return (
                    <td
                      key={im.level}
                      style={{ background: bandColor(score, cur.bands) }}
                    >
                      {score}
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
      </section>

      <div className="two-col">
        <section className="section">
          <h3>Probability levels ({cur.probability_levels.length})</h3>
          {cur.probability_levels.map((l, i) => (
            <div className="level-row" key={i}>
              <span className="lv">{l.level}</span>
              <input
                value={l.label}
                onChange={(e) => setProbLabel(i, e.target.value)}
              />
              <button className="link danger" onClick={() => removeProbLevel(i)}>
                Remove
              </button>
            </div>
          ))}
          <button className="btn" onClick={addProbLevel}>
            + Add level
          </button>
        </section>

        <section className="section">
          <h3>Impact levels ({cur.impact_levels.length})</h3>
          {cur.impact_levels.map((l, i) => (
            <div className="level-row" key={i}>
              <span className="lv">{l.level}</span>
              <input
                value={l.label}
                onChange={(e) => setImpLabel(i, e.target.value)}
              />
              <button className="link danger" onClick={() => removeImpLevel(i)}>
                Remove
              </button>
            </div>
          ))}
          <button className="btn" onClick={addImpLevel}>
            + Add level
          </button>
        </section>
      </div>

      <section className="section">
        <h3>Impact areas ({cur.impact_areas.length})</h3>
        {cur.impact_areas.map((a, ai) => (
          <div className="area-card" key={ai}>
            <div className="area-head">
              <input
                className="area-code"
                value={a.code}
                onChange={(e) => setArea(ai, "code", e.target.value)}
              />
              <input
                className="area-name"
                value={a.name}
                onChange={(e) => setArea(ai, "name", e.target.value)}
              />
              <button className="link danger" onClick={() => removeArea(ai)}>
                Remove area
              </button>
            </div>
            <div className="desc-grid">
              {cur.impact_levels.map((l, li) => (
                <label key={li} className="desc-cell">
                  <span>
                    {l.level} · {l.label}
                  </span>
                  <input
                    value={a.descriptors[li] ?? ""}
                    onChange={(e) => setDescriptor(ai, li, e.target.value)}
                  />
                </label>
              ))}
            </div>
          </div>
        ))}
        <button className="btn" onClick={addArea}>
          + Add area
        </button>
      </section>

      <section className="section">
        <h3>Risk bands (colored by probability × impact score)</h3>
        {cur.bands.map((b, i) => (
          <div className="band-row" key={i}>
            <input
              type="color"
              value={b.color}
              onChange={(e) => setBand(i, "color", e.target.value)}
            />
            <input
              className="band-name"
              value={b.name}
              onChange={(e) => setBand(i, "name", e.target.value)}
            />
            <label className="band-num">
              min
              <input
                type="number"
                value={b.min_score}
                onChange={(e) => setBand(i, "min_score", e.target.value)}
              />
            </label>
            <label className="band-num">
              max
              <input
                type="number"
                value={b.max_score}
                onChange={(e) => setBand(i, "max_score", e.target.value)}
              />
            </label>
            <button className="link danger" onClick={() => removeBand(i)}>
              Remove
            </button>
          </div>
        ))}
        <button className="btn" onClick={addBand}>
          + Add band
        </button>
      </section>

      <div className="save-bar">
        <button className="btn primary" disabled={saving} onClick={handleSave}>
          {saving ? "Saving…" : "Save scheme"}
        </button>
        {message && <span className="success inline">{message}</span>}
      </div>
    </div>
  );
}
