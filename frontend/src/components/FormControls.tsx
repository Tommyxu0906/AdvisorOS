import React from "react";
/** Field chrome and option lists shared by the intake, settings, and holdings editors. */

export const ACCOUNT_TYPES = [
  "cash",
  "taxable",
  "traditional_401k",
  "roth_401k",
  "traditional_ira",
  "roth_ira",
  "hsa",
  "other",
];

export const ASSET_CLASSES = [
  "us_equity",
  "intl_developed_equity",
  "emerging_equity",
  "bonds",
  "tips",
  "reit",
  "commodities",
  "crypto",
  "cash",
  "other",
];

export const RISK = [
  "conservative",
  "moderate_conservative",
  "moderate",
  "moderate_aggressive",
  "aggressive",
];

export const GOAL_TYPES = [
  "retirement",
  "home_purchase",
  "education",
  "emergency_fund",
  "wealth_growth",
  "income",
  "debt_payoff",
  "other",
];

/**
 * A labelled field, where the label is actually associated with the control.
 *
 * The previous version rendered a bare `<label>` next to its input. That looks identical and is
 * not the same thing: with no `for` and no wrapping, a screen reader announces the control as an
 * unnamed textbox, and clicking the label does not focus it. Both matter more than usual in a
 * form whose fields are someone's income and debts.
 *
 * The id is derived from the label so callers do not have to invent one, and the control is
 * cloned to receive it.
 */
export function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: React.ReactNode;
  /** One sentence on why the answer matters. Rendered under the control, not as a tooltip. */
  hint?: string;
}) {
  const id = `sf-${label.replace(/\W+/g, "-").toLowerCase()}`;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {React.isValidElement(children)
        ? React.cloneElement(children as React.ReactElement<{ id?: string }>, { id })
        : children}
      {hint && (
        <p className="tiny muted" style={{ margin: "4px 0 0" }}>
          {hint}
        </p>
      )}
    </div>
  );
}

export function RowEditor<T>({
  title,
  rows,
  onChange,
  blank,
  render,
  empty = "None.",
}: {
  title: string;
  rows: T[];
  onChange: (rows: T[]) => void;
  blank: T;
  render: (row: T, update: (next: T) => void) => React.ReactNode;
  empty?: string;
}) {
  return (
    <div className="row-editor">
      <div className="row-between">
        <h3>{title}</h3>
        <button className="secondary small" onClick={() => onChange([...rows, { ...blank }])}>
          + add
        </button>
      </div>
      {rows.length === 0 && <p className="muted small">{empty}</p>}
      {rows.map((row, i) => (
        <div className="editor-row" key={i}>
          {render(row, (next) => onChange(rows.map((r, j) => (j === i ? next : r))))}
          <button
            className="secondary small"
            onClick={() => onChange(rows.filter((_, j) => j !== i))}
            aria-label={`remove ${title} row ${i + 1}`}
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
