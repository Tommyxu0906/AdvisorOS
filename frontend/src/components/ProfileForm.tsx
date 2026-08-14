import type { HoldingDraft, ProfileDraft } from "../lib/draft";
import { missingFields, num, str } from "../lib/draft";
import type { QuoteState } from "../lib/useQuotes";
import type { SaveStatus } from "../lib/useSavedProfile";

interface Props {
  profile: ProfileDraft;
  holdings: HoldingDraft[];
  question: string;
  quotes: QuoteState;
  saveStatus: SaveStatus;
  saveError: string | null;
  onProfile: (p: ProfileDraft) => void;
  onHoldings: (h: HoldingDraft[]) => void;
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

const GOAL_TYPES = [
  "retirement",
  "home_purchase",
  "education",
  "emergency_fund",
  "wealth_growth",
  "income",
  "debt_payoff",
  "other",
];

export function ProfileForm({
  profile,
  holdings,
  question,
  quotes,
  saveStatus,
  saveError,
  onProfile,
  onHoldings,
  onQuestion,
}: Props) {
  const set = <K extends keyof ProfileDraft>(key: K, value: ProfileDraft[K]) =>
    onProfile({ ...profile, [key]: value });

  const missing = missingFields(profile);

  return (
    <section className="panel">
      <div className="row-between">
        <h2>Your situation</h2>
        <SaveBadge status={saveStatus} />
      </div>
      <p className="muted">
        These are your numbers, not an example — nothing is filled in for you, because a guessed
        figure produces a confident answer to the wrong question. Nothing here is sent to Claude
        until you run the committee.
      </p>
      {saveError && <p className="error">{saveError}</p>}

      <div className="grid">
        <Field label="Age">
          <input
            type="number"
            placeholder="34"
            min={16}
            max={120}
            value={str(profile.age)}
            onChange={(e) => set("age", num(e.target.value))}
          />
        </Field>
        <Field label="Dependents">
          <input
            type="number"
            placeholder="0"
            min={0}
            value={str(profile.dependents)}
            onChange={(e) => set("dependents", num(e.target.value))}
          />
        </Field>
        <Field label="Annual gross income">
          <input
            type="number"
            placeholder="before tax"
            min={0}
            value={str(profile.income.annual_gross)}
            onChange={(e) =>
              set("income", { ...profile.income, annual_gross: num(e.target.value) })
            }
          />
        </Field>
        <Field label="Employer match (fraction of salary)">
          <input
            type="number"
            step="0.01"
            placeholder="0.04 — blank means none"
            min={0}
            max={1}
            value={str(profile.income.employer_match_pct)}
            onChange={(e) =>
              set("income", { ...profile.income, employer_match_pct: num(e.target.value) })
            }
          />
        </Field>
        <Field label="Monthly essential expenses">
          <input
            type="number"
            placeholder="rent, food, utilities, minimums"
            min={0}
            value={str(profile.expenses.monthly_essential)}
            onChange={(e) =>
              set("expenses", { ...profile.expenses, monthly_essential: num(e.target.value) })
            }
          />
        </Field>
        <Field label="Monthly discretionary">
          <input
            type="number"
            placeholder="everything else"
            min={0}
            value={str(profile.expenses.monthly_discretionary)}
            onChange={(e) =>
              set("expenses", {
                ...profile.expenses,
                monthly_discretionary: num(e.target.value),
              })
            }
          />
        </Field>
        <Field label="Risk tolerance">
          <select
            value={profile.risk_tolerance}
            onChange={(e) => set("risk_tolerance", e.target.value)}
          >
            <option value="">— choose —</option>
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
            placeholder="0 = novice, 1 = professional"
            value={str(profile.self_reported_experience)}
            onChange={(e) => set("self_reported_experience", num(e.target.value))}
          />
        </Field>
      </div>

      <RowEditor
        title="Debts"
        rows={profile.debts}
        onChange={(rows) => set("debts", rows)}
        blank={{ name: "", balance: null, apr: null, minimum_monthly_payment: null }}
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
              value={str(row.balance)}
              onChange={(e) => update({ ...row, balance: num(e.target.value) })}
            />
            <input
              type="number"
              step="0.001"
              placeholder="APR as 0.229"
              value={str(row.apr)}
              onChange={(e) => update({ ...row, apr: num(e.target.value) })}
            />
            <input
              type="number"
              placeholder="min payment"
              value={str(row.minimum_monthly_payment)}
              onChange={(e) => update({ ...row, minimum_monthly_payment: num(e.target.value) })}
            />
          </>
        )}
      />

      <RowEditor
        title="Assets"
        rows={profile.assets}
        onChange={(rows) => set("assets", rows)}
        blank={{ name: "", value: null, account_type: "cash", is_liquid: true }}
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
              value={str(row.value)}
              onChange={(e) => update({ ...row, value: num(e.target.value) })}
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
        blank={{ name: "", goal_type: "other", years_until_needed: null, priority: null }}
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
              {GOAL_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <input
              type="number"
              step="0.5"
              placeholder="years away"
              value={str(row.years_until_needed)}
              onChange={(e) => update({ ...row, years_until_needed: num(e.target.value) })}
            />
            <input
              type="number"
              min={1}
              max={5}
              placeholder="priority 1–5"
              value={str(row.priority)}
              onChange={(e) => update({ ...row, priority: num(e.target.value) })}
            />
          </>
        )}
      />

      <RowEditor
        title="Portfolio holdings"
        rows={holdings}
        onChange={onHoldings}
        blank={{ symbol: "", asset_class: "us_equity", quantity: null, market_value: null }}
        render={(row, update) => {
          const quote = quotes.quotes[row.symbol.trim().toUpperCase()];
          return (
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
                step="any"
                placeholder="shares"
                min={0}
                value={str(row.quantity)}
                onChange={(e) => {
                  const quantity = num(e.target.value);
                  // With a share count and a live price, the value follows the market instead
                  // of whatever it was worth the day it was typed in.
                  update({
                    ...row,
                    quantity,
                    market_value:
                      quantity !== null && quote
                        ? Number((quantity * quote.price).toFixed(2))
                        : row.market_value,
                  });
                }}
              />
              <input
                type="number"
                placeholder="market value"
                min={0}
                value={str(row.market_value)}
                onChange={(e) => update({ ...row, market_value: num(e.target.value) })}
              />
              <PriceTag quote={quote} loading={quotes.loading} symbol={row.symbol} />
            </>
          );
        }}
      />

      <label htmlFor="question">Your question</label>
      <textarea
        id="question"
        rows={3}
        value={question}
        onChange={(e) => onQuestion(e.target.value)}
        placeholder="Should I sell some NVDA to pay off my credit card?"
      />

      {missing.length > 0 && (
        <p className="muted small">
          Still needed before the analysis can run: {missing.join(", ")}.
        </p>
      )}
    </section>
  );
}

function SaveBadge({ status }: { status: SaveStatus }) {
  // Signed out is not a failure state — it is the anonymous tier working as designed, so it
  // says what would change rather than warning about what is missing.
  if (status === "anonymous")
    return <span className="badge free">not saved — sign in to keep this</span>;
  if (status === "loading") return <span className="badge free">loading your profile…</span>;
  if (status === "saving") return <span className="badge free">saving…</span>;
  if (status === "error") return <span className="badge risk">not saved</span>;
  return <span className="badge free">saved to your account</span>;
}

function PriceTag({
  quote,
  loading,
  symbol,
}: {
  quote: { price: number; change_pct: number | null } | undefined;
  loading: boolean;
  symbol: string;
}) {
  if (!symbol.trim()) return <span className="muted small" />;
  if (!quote) return <span className="muted small">{loading ? "…" : "no price"}</span>;
  return (
    <span className={`small${quote.change_pct !== null && quote.change_pct < 0 ? " risk" : ""}`}>
      ${quote.price.toFixed(2)}
      {quote.change_pct !== null &&
        ` ${quote.change_pct >= 0 ? "+" : ""}${(quote.change_pct * 100).toFixed(2)}%`}
    </span>
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
