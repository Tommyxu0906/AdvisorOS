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

export function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="field">
      <label>{label}</label>
      {children}
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
