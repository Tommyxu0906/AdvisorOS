import type { PortfolioInput, ProfileInput } from "../types";

interface Props {
  profile: ProfileInput;
  portfolio: PortfolioInput;
  question: string;
  onProfile: (p: ProfileInput) => void;
  onPortfolio: (p: PortfolioInput) => void;
  onQuestion: (q: string) => void;
}

const ACCOUNT_TYPES = [
  "cash",
  "taxable",
  "traditional_401k",
  "roth_401k",
  "traditional_ira",
  "roth_ira",
  "hsa",
  "other",
];

const ASSET_CLASSES = [
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

const RISK = [
  "conservative",
  "moderate_conservative",
  "moderate",
  "moderate_aggressive",
  "aggressive",
];

export function ProfileForm({
  profile,
  portfolio,
  question,
  onProfile,
  onPortfolio,
  onQuestion,
}: Props) {
  const set = <K extends keyof ProfileInput>(key: K, value: ProfileInput[K]) =>
    onProfile({ ...profile, [key]: value });

  return (
    <section className="panel">
      <h2>Your situation</h2>
      <p className="muted">
        Nothing here is sent to Claude until you run the committee. The analysis below is
        computed locally by the server with no model involved.
      </p>

      <div className="grid">
        <Field label="Age">
          <input
            type="number"
            value={profile.age}
            onChange={(e) => set("age", Number(e.target.value))}
          />
        </Field>
        <Field label="Dependents">
          <input
            type="number"
            value={profile.dependents}
            onChange={(e) => set("dependents", Number(e.target.value))}
          />
        </Field>
        <Field label="Annual gross income">
          <input
            type="number"
            value={profile.income.annual_gross}
            onChange={(e) =>
              set("income", { ...profile.income, annual_gross: Number(e.target.value) })
            }
          />
        </Field>
        <Field label="Employer match (% of salary)">
          <input
            type="number"
            step="0.01"
            value={profile.income.employer_match_pct}
            onChange={(e) =>
              set("income", { ...profile.income, employer_match_pct: Number(e.target.value) })
            }
          />
        </Field>
        <Field label="Monthly essential expenses">
          <input
            type="number"
            value={profile.expenses.monthly_essential}
            onChange={(e) =>
              set("expenses", { ...profile.expenses, monthly_essential: Number(e.target.value) })
            }
          />
        </Field>
        <Field label="Monthly discretionary">
          <input
            type="number"
            value={profile.expenses.monthly_discretionary}
            onChange={(e) =>
              set("expenses", {
                ...profile.expenses,
                monthly_discretionary: Number(e.target.value),
              })
            }
          />
        </Field>
        <Field label="Risk tolerance">
          <select
            value={profile.risk_tolerance}
            onChange={(e) => set("risk_tolerance", e.target.value)}
          >
            {RISK.map((r) => (
              <option key={r} value={r}>
                {r.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Investing experience (0–1)">
          <input
            type="number"
            step="0.05"
            min={0}
            max={1}
            value={profile.self_reported_experience}
            onChange={(e) => set("self_reported_experience", Number(e.target.value))}
          />
        </Field>
      </div>

      <RowEditor
        title="Debts"
        rows={profile.debts}
        onChange={(rows) => set("debts", rows)}
        blank={{ name: "", balance: 0, apr: 0.0, minimum_monthly_payment: 0 }}
        render={(row, update) => (
          <>
            <input
              placeholder="name"
              value={row.name}
              onChange={(e) => update({ ...row, name: e.target.value })}
            />
            <input
              type="number"
              placeholder="balance"
              value={row.balance}
              onChange={(e) => update({ ...row, balance: Number(e.target.value) })}
            />
            <input
              type="number"
              step="0.001"
              placeholder="APR (0.229)"
              value={row.apr}
              onChange={(e) => update({ ...row, apr: Number(e.target.value) })}
            />
            <input
              type="number"
              placeholder="min payment"
              value={row.minimum_monthly_payment}
              onChange={(e) => update({ ...row, minimum_monthly_payment: Number(e.target.value) })}
            />
          </>
        )}
      />

      <RowEditor
        title="Assets"
        rows={profile.assets}
        onChange={(rows) => set("assets", rows)}
        blank={{ name: "", value: 0, account_type: "cash", is_liquid: true }}
        render={(row, update) => (
          <>
            <input
              placeholder="name"
              value={row.name}
              onChange={(e) => update({ ...row, name: e.target.value })}
            />
            <input
              type="number"
              placeholder="value"
              value={row.value}
              onChange={(e) => update({ ...row, value: Number(e.target.value) })}
            />
            <select
              value={row.account_type}
              onChange={(e) => update({ ...row, account_type: e.target.value })}
            >
              {ACCOUNT_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={row.is_liquid}
                onChange={(e) => update({ ...row, is_liquid: e.target.checked })}
              />
              liquid
            </label>
          </>
        )}
      />

      <RowEditor
        title="Goals"
        rows={profile.goals}
        onChange={(rows) => set("goals", rows)}
        blank={{ name: "", goal_type: "other", years_until_needed: 5, priority: 3 }}
        render={(row, update) => (
          <>
            <input
              placeholder="name"
              value={row.name}
              onChange={(e) => update({ ...row, name: e.target.value })}
            />
            <select
              value={row.goal_type}
              onChange={(e) => update({ ...row, goal_type: e.target.value })}
            >
              {[
                "retirement",
                "home_purchase",
                "education",
                "emergency_fund",
                "wealth_growth",
                "income",
                "debt_payoff",
                "other",
              ].map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <input
              type="number"
              step="0.5"
              placeholder="years away"
              value={row.years_until_needed}
              onChange={(e) => update({ ...row, years_until_needed: Number(e.target.value) })}
            />
            <input
              type="number"
              min={1}
              max={5}
              placeholder="priority"
              value={row.priority}
              onChange={(e) => update({ ...row, priority: Number(e.target.value) })}
            />
          </>
        )}
      />

      <RowEditor
        title="Portfolio holdings"
        rows={portfolio.holdings}
        onChange={(rows) => onPortfolio({ holdings: rows })}
        blank={{ symbol: "", asset_class: "us_equity", market_value: 0 }}
        render={(row, update) => (
          <>
            <input
              placeholder="symbol"
              value={row.symbol}
              onChange={(e) => update({ ...row, symbol: e.target.value.toUpperCase() })}
            />
            <select
              value={row.asset_class}
              onChange={(e) => update({ ...row, asset_class: e.target.value })}
            >
              {ASSET_CLASSES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <input
              type="number"
              placeholder="market value"
              value={row.market_value}
              onChange={(e) => update({ ...row, market_value: Number(e.target.value) })}
            />
          </>
        )}
      />

      <label htmlFor="question">Your question</label>
      <textarea
        id="question"
        rows={3}
        value={question}
        onChange={(e) => onQuestion(e.target.value)}
        placeholder="Should I sell some NVDA to pay off my credit card?"
      />
    </section>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="field">
      <label>{label}</label>
      {children}
    </div>
  );
}

function RowEditor<T>({
  title,
  rows,
  onChange,
  blank,
  render,
}: {
  title: string;
  rows: T[];
  onChange: (rows: T[]) => void;
  blank: T;
  render: (row: T, update: (next: T) => void) => React.ReactNode;
}) {
  return (
    <div className="row-editor">
      <div className="row-between">
        <h3>{title}</h3>
        <button className="secondary small" onClick={() => onChange([...rows, { ...blank }])}>
          + add
        </button>
      </div>
      {rows.length === 0 && <p className="muted small">None.</p>}
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
