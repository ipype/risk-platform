import type { ReactNode } from "react";
import DistributionPicker from "./DistributionPicker";
import DistributionPreview from "./DistributionPreview";
import PointsEditor from "./PointsEditor";
import RationaleField from "./RationaleField";
import { RATIONALE_LABELS, slotsFor } from "../../quant/draft";
import type { DraftDimension, DraftRationale } from "../../quant/draft";
import type {
  DimensionSummary,
  DistName,
  DistributionGuidance,
  QuantIssue,
  QuantPoint,
  RationaleKey,
} from "../../quant/types";

/**
 * One impact dimension — cost or schedule.
 *
 * Shape is chosen per dimension because the two genuinely differ: a delay capped by a
 * contractual milestone is a triangular whose bound means something, while the cost it
 * drags along is unbounded and PERT. The controls follow from the shape's `inputs` kind
 * rather than from a switch on its name, so a shape added on the server renders correctly
 * here without a matching change.
 *
 * Every number carries its own rationale. It is the field a reviewer reads first and the
 * one that decides whether an estimate survives challenge — and it is where an agent's
 * draft will land, which is why provenance sits on each entry rather than on the estimate.
 */

interface Props {
  title: string;
  unit: string;
  value: DraftDimension;
  options: DistributionGuidance[];
  summary: DimensionSummary | null;
  issues: QuantIssue[];
  disabled?: boolean;
  onChange: (next: DraftDimension) => void;
  children?: ReactNode;
}

const SLOT_FIELD: Record<RationaleKey, "min" | "ml" | "max"> = {
  min: "min",
  ml: "ml",
  max: "max",
};

export default function DimensionEditor({
  title,
  unit,
  value,
  options,
  summary,
  issues,
  disabled,
  onChange,
  children,
}: Props) {
  const guidance = options.find((o) => o.value === value.dist);
  const inputs = guidance?.inputs ?? "none";
  const slots = slotsFor(value.dist);
  const assessed = value.dist !== "none";

  const set = (patch: Partial<DraftDimension>) => onChange({ ...value, ...patch });

  const setRationale = (key: RationaleKey, next: DraftRationale) =>
    onChange({ ...value, rationale: { ...value.rationale, [key]: next } });

  const messageFor = (suffix: string): string | null =>
    issues.find((i) => i.field.endsWith(`.${suffix}`) && i.severity === "error")?.message ?? null;

  const pointIssue = issues.find((i) => i.field.endsWith(".points"));
  const distIssue = messageFor("dist");

  const parsedPoints: QuantPoint[] | null =
    inputs === "points"
      ? value.points
          .map((p) => ({ x: Number(p.x), p: Number(p.p) }))
          .filter((p) => Number.isFinite(p.x) && Number.isFinite(p.p))
      : null;

  return (
    <section className="qnt-dimension">
      <header className="qnt-dimension-head">
        <h3 className="qnt-dimension-title">{title}</h3>
        {!assessed && <span className="qnt-muted">Not assessed</span>}
      </header>

      <DistributionPicker
        value={value.dist}
        options={options}
        disabled={disabled}
        onChange={(dist: DistName) => set({ dist })}
      />
      {distIssue && <p className="qnt-error">{distIssue}</p>}

      {children}

      {(inputs === "three_point" || inputs === "bounds_only") && (
        <div className="qnt-slots">
          {slots.map((slot) => {
            const field = SLOT_FIELD[slot];
            const err = messageFor(field);
            return (
              <div className="qnt-slot" key={slot}>
                <label className="qnt-field">
                  <span className="qnt-label">{RATIONALE_LABELS[slot]}</span>
                  <input
                    className={err ? "qnt-input qnt-input-bad" : "qnt-input"}
                    inputMode="decimal"
                    value={value[field]}
                    disabled={disabled}
                    placeholder={unit}
                    onChange={(e) => set({ [field]: e.target.value } as Partial<DraftDimension>)}
                  />
                </label>
                <RationaleField
                  label={RATIONALE_LABELS[slot]}
                  value={value.rationale[slot]}
                  disabled={disabled}
                  onChange={(next) => setRationale(slot, next)}
                />
                {err && <p className="qnt-error">{err}</p>}
              </div>
            );
          })}
        </div>
      )}

      {value.dist === "pert" && (
        <label className="qnt-field qnt-field-inline">
          <span className="qnt-label">
            Lambda
            <span
              className="qnt-hint"
              title="How tightly weight gathers on the most likely value. Four is standard. Raising it narrows the spread and lowers contingency — never tune it to reach a number you already had in mind."
            >
              ?
            </span>
          </span>
          <input
            className="qnt-input qnt-input-sm"
            inputMode="decimal"
            value={value.pertLambda}
            disabled={disabled}
            onChange={(e) => set({ pertLambda: e.target.value })}
          />
        </label>
      )}

      {inputs === "points" && (
        <>
          <PointsEditor
            dist={value.dist}
            points={value.points}
            unit={unit}
            disabled={disabled}
            onChange={(points) => set({ points })}
          />
          {pointIssue && (
            <p className={pointIssue.severity === "error" ? "qnt-error" : "qnt-warn"}>
              {pointIssue.message}
            </p>
          )}
        </>
      )}

      {summary && <DistributionPreview summary={summary} points={parsedPoints} unit={unit} />}
    </section>
  );
}
