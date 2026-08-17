/**
 * Setup, as six short questions instead of one long form.
 *
 * The old page put age, dependents, income, expenses, employer match, risk tolerance, experience
 * and three repeating tables on one screen. Every field was legitimate; presenting them together
 * made the product look like a database admin panel and made abandoning it the rational choice.
 *
 * Two things matter more than the step count.
 *
 * **Units are human.** Employer match is entered as `4%`, not `0.04`. APR is `22.9%`, not
 * `0.229`. Experience is a named level, not a float. The API still receives the fractions it
 * always did — `lib/units.ts` converts at the edge — because changing a backend schema to make a
 * form nicer is the wrong direction of travel.
 *
 * **Required and optional are visible.** Blank employer match genuinely means "no match", and
 * blank debts genuinely means "no debt". Those are answers. Marking them optional is what lets
 * someone move quickly without wondering whether they have broken something.
 */

import React, { useState } from "react";
import type { AssetDraft, DebtDraft, GoalDraft, HoldingDraft, ProfileDraft } from "../lib/draft";
import { REQUIRED_FIELDS } from "../lib/draft";
import {
  EXPERIENCE_LEVELS,
  GOAL_PRIORITIES,
  humanAccountType,
  money,
  toFraction,
  toPercent,
} from "../lib/units";
import { Card, InlineAlert, SectionHeader, Stepper } from "../ui";
import { HoldingsEditor } from "../components/HoldingsEditor";
import type { QuoteState } from "../lib/useQuotes";

const STEPS = ["Basics", "Cash flow", "Balance sheet", "Goals", "Portfolio", "Review"];

const RISK_LEVELS = [
  { value: "conservative", label: "Conservative", hint: "Protect what I have" },
  { value: "moderate_conservative", label: "Cautious", hint: "Mostly steady, some growth" },
  { value: "moderate", label: "Balanced", hint: "Accept normal market swings" },
  { value: "moderate_aggressive", label: "Growth", hint: "Comfortable with real drops" },
  { value: "aggressive", label: "Aggressive", hint: "Maximum growth, deep drawdowns fine" },
];

// Mirrors backend AccountType exactly. A value this list invents is rejected by the API at the
// first analysis call, which is how the demo household shipped with a "checking" account.
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

const GOAL_TYPES = [
  { value: "retirement", label: "Retirement" },
  { value: "home_purchase", label: "Home purchase" },
  { value: "education", label: "Education" },
  { value: "emergency_fund", label: "Emergency fund" },
  { value: "wealth_growth", label: "Wealth growth" },
  { value: "income", label: "Income" },
  { value: "debt_payoff", label: "Debt payoff" },
  { value: "other", label: "Other" },
];

export function OnboardingFlow({
  profile,
  holdings,
  quotes,
  onProfile,
  onHoldings,
  onDone,
}: {
  profile: ProfileDraft;
  holdings: HoldingDraft[];
  quotes: QuoteState;
  onProfile: (p: ProfileDraft) => void;
  onHoldings: (h: HoldingDraft[]) => void;
  onDone: () => void;
}) {
  const [step, setStep] = useState(0);

  const patch = (changes: Partial<ProfileDraft>) => onProfile({ ...profile, ...changes });
  const num = (value: string): number | null => (value.trim() === "" ? null : Number(value));

  // Per-step gating, so someone cannot arrive at Review with a hole in step one.
  const blocked = stepBlockers(profile, step);
  const isLast = step === STEPS.length - 1;

  return (
    <div className="page" style={{ maxWidth: 780 }}>
      <div className="page-head">
        <h1>Set up your situation</h1>
        <p className="lede">
          Six short steps. Nothing is filled in for you, because a guessed figure produces a
          confident answer to the wrong question.
        </p>
      </div>

      <Stepper steps={STEPS} current={step} onJump={setStep} />

      <Card>
        {step === 0 && (
          <>
            <SectionHeader title="Basics" hint="Who the analysis is for." />
            <div className="grid">
              <Field label="Age" required>
                <input
                  type="number"
                  inputMode="numeric"
                  value={profile.age ?? ""}
                  onChange={(e) => patch({ age: num(e.target.value) })}
                />
              </Field>
              <Field label="People who depend on your income" required>
                <input
                  type="number"
                  inputMode="numeric"
                  value={profile.dependents ?? ""}
                  onChange={(e) => patch({ dependents: num(e.target.value) })}
                  placeholder="0"
                />
              </Field>
            </div>

            <Choices
              legend="Risk tolerance"
              required
              options={RISK_LEVELS}
              selected={profile.risk_tolerance}
              onSelect={(value) => patch({ risk_tolerance: String(value) })}
            />

            <Choices
              legend="Investing experience"
              required
              options={EXPERIENCE_LEVELS.map((l) => ({ ...l, value: l.value }))}
              selected={nearestExperience(profile.self_reported_experience)}
              onSelect={(value) => patch({ self_reported_experience: Number(value) })}
            />
          </>
        )}

        {step === 1 && (
          <>
            <SectionHeader title="Cash flow" hint="What comes in, and what has to go out." />
            <div className="grid">
              <Field label="Annual gross income" required hint="Before tax">
                <Affix prefix="$">
                  <input
                    type="number"
                    inputMode="decimal"
                    value={profile.income.annual_gross ?? ""}
                    onChange={(e) =>
                      patch({
                        income: { ...profile.income, annual_gross: num(e.target.value) },
                      })
                    }
                  />
                </Affix>
              </Field>

              {/* The field this whole file exists for: 4%, not 0.04. */}
              <Field
                label="Employer retirement match"
                hint="As a share of salary. Blank means none."
              >
                <Affix suffix="%">
                  <input
                    type="number"
                    inputMode="decimal"
                    step="0.1"
                    placeholder="4"
                    value={toPercent(profile.income.employer_match_pct) ?? ""}
                    onChange={(e) =>
                      patch({
                        income: {
                          ...profile.income,
                          employer_match_pct: toFraction(num(e.target.value)),
                        },
                      })
                    }
                  />
                </Affix>
              </Field>

              <Field label="Monthly essential spending" required hint="Rent, food, utilities, minimums">
                <Affix prefix="$">
                  <input
                    type="number"
                    inputMode="decimal"
                    value={profile.expenses.monthly_essential ?? ""}
                    onChange={(e) =>
                      patch({
                        expenses: { ...profile.expenses, monthly_essential: num(e.target.value) },
                      })
                    }
                  />
                </Affix>
              </Field>

              <Field label="Monthly discretionary spending" required hint="Everything else">
                <Affix prefix="$">
                  <input
                    type="number"
                    inputMode="decimal"
                    value={profile.expenses.monthly_discretionary ?? ""}
                    onChange={(e) =>
                      patch({
                        expenses: {
                          ...profile.expenses,
                          monthly_discretionary: num(e.target.value),
                        },
                      })
                    }
                  />
                </Affix>
              </Field>
            </div>
          </>
        )}

        {step === 2 && (
          <>
            <SectionHeader
              title="Balance sheet"
              hint="Debts and cash. Both optional — empty is a real answer."
            />
            <Repeater<DebtDraft>
              legend="Debts"
              rows={profile.debts}
              empty="No debt recorded."
              addLabel="Add a debt"
              blank={{ name: "", balance: null, apr: null, minimum_monthly_payment: null }}
              onChange={(debts) => patch({ debts })}
              render={(row, update) => (
                <>
                  <Field label="Name">
                    <input
                      value={row.name}
                      placeholder="Credit card"
                      onChange={(e) => update({ name: e.target.value })}
                    />
                  </Field>
                  <Field label="Balance">
                    <Affix prefix="$">
                      <input
                        type="number"
                        inputMode="decimal"
                        value={row.balance ?? ""}
                        onChange={(e) => update({ balance: num(e.target.value) })}
                      />
                    </Affix>
                  </Field>
                  {/* 22.9%, never 0.229. */}
                  <Field label="Interest rate">
                    <Affix suffix="%">
                      <input
                        type="number"
                        inputMode="decimal"
                        step="0.1"
                        placeholder="22.9"
                        value={toPercent(row.apr) ?? ""}
                        onChange={(e) => update({ apr: toFraction(num(e.target.value)) })}
                      />
                    </Affix>
                  </Field>
                  <Field label="Minimum payment">
                    <Affix prefix="$">
                      <input
                        type="number"
                        inputMode="decimal"
                        value={row.minimum_monthly_payment ?? ""}
                        onChange={(e) =>
                          update({ minimum_monthly_payment: num(e.target.value) })
                        }
                      />
                    </Affix>
                  </Field>
                </>
              )}
            />

            <hr className="divider" />

            <Repeater<AssetDraft>
              legend="Cash and accounts"
              rows={profile.assets}
              empty="No accounts recorded."
              addLabel="Add an account"
              blank={{ name: "", value: null, account_type: "cash", is_liquid: true }}
              onChange={(assets) => patch({ assets })}
              render={(row, update) => (
                <>
                  <Field label="Name">
                    <input
                      value={row.name}
                      placeholder="Emergency savings"
                      onChange={(e) => update({ name: e.target.value })}
                    />
                  </Field>
                  <Field label="Value">
                    <Affix prefix="$">
                      <input
                        type="number"
                        inputMode="decimal"
                        value={row.value ?? ""}
                        onChange={(e) => update({ value: num(e.target.value) })}
                      />
                    </Affix>
                  </Field>
                  <Field label="Account type">
                    <select
                      value={row.account_type}
                      onChange={(e) => update({ account_type: e.target.value })}
                    >
                      {ACCOUNT_TYPES.map((t) => (
                        <option key={t} value={t}>
                          {humanAccountType(t)}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Availability">
                    <label className="checkbox">
                      <input
                        type="checkbox"
                        checked={row.is_liquid}
                        onChange={(e) => update({ is_liquid: e.target.checked })}
                      />
                      Can be spent this month
                    </label>
                  </Field>
                </>
              )}
            />
          </>
        )}

        {step === 3 && (
          <>
            <SectionHeader
              title="Goals"
              hint="A goal and its horizon change which advisors get selected, and how much risk the analysis will tolerate."
            />
            <Repeater<GoalDraft>
              legend="Goals"
              rows={profile.goals}
              empty="No goals yet."
              addLabel="Add a goal"
              blank={{
                name: "",
                goal_type: "retirement",
                years_until_needed: null,
                priority: 2,
              }}
              onChange={(goals) => patch({ goals })}
              render={(row, update) => (
                <>
                  <Field label="Name">
                    <input
                      value={row.name}
                      placeholder="Home down payment"
                      onChange={(e) => update({ name: e.target.value })}
                    />
                  </Field>
                  <Field label="Type">
                    <select
                      value={row.goal_type}
                      onChange={(e) => update({ goal_type: e.target.value })}
                    >
                      {GOAL_TYPES.map((t) => (
                        <option key={t.value} value={t.value}>
                          {t.label}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Years away">
                    <input
                      type="number"
                      inputMode="decimal"
                      step="0.5"
                      value={row.years_until_needed ?? ""}
                      onChange={(e) => update({ years_until_needed: num(e.target.value) })}
                    />
                  </Field>
                  {/* Descriptive, not 1-5. The backend still receives the number. */}
                  <Field label="How firm is it">
                    <select
                      value={row.priority ?? 2}
                      onChange={(e) => update({ priority: Number(e.target.value) })}
                    >
                      {GOAL_PRIORITIES.map((p) => (
                        <option key={p.value} value={p.value}>
                          {p.label} — {p.hint}
                        </option>
                      ))}
                    </select>
                  </Field>
                </>
              )}
            />
          </>
        )}

        {step === 4 && (
          <>
            <SectionHeader
              title="Portfolio"
              hint="Add what you hold, or skip and add it later — the analysis works either way."
            />
            <HoldingsEditor holdings={holdings} quotes={quotes} onHoldings={onHoldings} />
          </>
        )}

        {step === 5 && (
          <>
            <SectionHeader title="Review" hint="Nothing has been sent anywhere yet." />
            <ReviewTable profile={profile} holdings={holdings} onJump={setStep} />
          </>
        )}

        {blocked.length > 0 && step !== 5 && (
          <div style={{ marginTop: 18 }}>
            <InlineAlert tone="warn" title="Still needed on this step">
              {blocked.join(", ")}
            </InlineAlert>
          </div>
        )}

        <div className="row-between" style={{ marginTop: 24 }}>
          <button
            className="secondary"
            onClick={() => setStep((s) => Math.max(0, s - 1))}
            disabled={step === 0}
          >
            Back
          </button>
          <button
            className="primary"
            disabled={blocked.length > 0}
            onClick={() => (isLast ? onDone() : setStep((s) => s + 1))}
          >
            {isLast ? "Go to my decision workspace" : "Continue"}
          </button>
        </div>
      </Card>
    </div>
  );
}

// --- pieces ------------------------------------------------------------------------------------

function Field({
  label,
  hint,
  required,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  const id = `f-${label.replace(/\W+/g, "-").toLowerCase()}`;
  return (
    <div className="field">
      <label htmlFor={id}>
        {label}
        {required ? (
          <span className="required-dot" aria-label="required">
            *
          </span>
        ) : (
          <span className="optional"> · optional</span>
        )}
      </label>
      {/* The control is cloned so the label's `for` actually points at it — a wrapper div with a
          label beside it looks right and reads as unlabelled to a screen reader. */}
      {React.isValidElement(children)
        ? React.cloneElement(children as React.ReactElement<{ id?: string }>, { id })
        : children}
      {hint && <p className="tiny muted" style={{ margin: "4px 0 0" }}>{hint}</p>}
    </div>
  );
}

function Affix({
  prefix,
  suffix,
  children,
}: {
  prefix?: string;
  suffix?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={`input-affix${prefix ? " prefix" : ""}`}>
      {children}
      <span className="affix">{prefix ?? suffix}</span>
    </div>
  );
}

function Choices({
  legend,
  options,
  selected,
  onSelect,
  required,
}: {
  legend: string;
  options: readonly { value: string | number; label: string; hint?: string }[];
  selected: string | number | "";
  onSelect: (value: string | number) => void;
  required?: boolean;
}) {
  return (
    <fieldset style={{ border: 0, padding: 0, margin: "20px 0 0" }}>
      <legend style={{ padding: 0 }}>
        <span className="metric-label">
          {legend}
          {required && <span className="required-dot">*</span>}
        </span>
      </legend>
      <div className="choice-set" style={{ marginTop: 8 }}>
        {options.map((o) => (
          <button
            key={o.value}
            type="button"
            className={`choice${selected === o.value ? " selected" : ""}`}
            aria-pressed={selected === o.value}
            onClick={() => onSelect(o.value)}
          >
            <span className="choice-label">{o.label}</span>
            {o.hint && <span className="choice-hint">{o.hint}</span>}
          </button>
        ))}
      </div>
    </fieldset>
  );
}

function Repeater<T>({
  legend,
  rows,
  blank,
  empty,
  addLabel,
  onChange,
  render,
}: {
  legend: string;
  rows: T[];
  blank: T;
  empty: string;
  addLabel: string;
  onChange: (rows: T[]) => void;
  render: (row: T, update: (changes: Partial<T>) => void) => React.ReactNode;
}) {
  return (
    <div>
      <div className="row-between" style={{ marginBottom: 10 }}>
        <h3>{legend}</h3>
        <button className="secondary" onClick={() => onChange([...rows, structuredClone(blank)])}>
          {addLabel}
        </button>
      </div>

      {rows.length === 0 && <p className="small muted">{empty}</p>}

      {rows.map((row, i) => (
        <div key={i} className="card card-sunk" style={{ marginBottom: 10, padding: 14 }}>
          <div className="grid">
            {render(row, (changes) =>
              onChange(rows.map((r, j) => (i === j ? { ...r, ...changes } : r))),
            )}
          </div>
          <button
            className="linklike small"
            style={{ marginTop: 10 }}
            onClick={() => onChange(rows.filter((_, j) => j !== i))}
          >
            Remove
          </button>
        </div>
      ))}
    </div>
  );
}

function ReviewTable({
  profile,
  holdings,
  onJump,
}: {
  profile: ProfileDraft;
  holdings: HoldingDraft[];
  onJump: (step: number) => void;
}) {
  const rows: { label: string; value: string; step: number }[] = [
    { label: "Age", value: profile.age?.toString() ?? "—", step: 0 },
    { label: "Dependents", value: profile.dependents?.toString() ?? "—", step: 0 },
    {
      label: "Risk tolerance",
      value: RISK_LEVELS.find((r) => r.value === profile.risk_tolerance)?.label ?? "—",
      step: 0,
    },
    {
      label: "Annual income",
      value: money(profile.income.annual_gross),
      step: 1,
    },
    {
      label: "Monthly spending",
      value: money(
        (profile.expenses.monthly_essential ?? 0) + (profile.expenses.monthly_discretionary ?? 0),
      ),
      step: 1,
    },
    { label: "Debts", value: `${profile.debts.length} recorded`, step: 2 },
    { label: "Accounts", value: `${profile.assets.length} recorded`, step: 2 },
    { label: "Goals", value: `${profile.goals.length} recorded`, step: 3 },
    { label: "Holdings", value: `${holdings.length} positions`, step: 4 },
  ];

  return (
    <div className="table-scroll">
      <table>
        <tbody>
          {rows.map((r) => (
            <tr key={r.label}>
              <td className="muted" style={{ width: "45%" }}>
                {r.label}
              </td>
              <td>{r.value}</td>
              <td className="num">
                <button className="linklike small" onClick={() => onJump(r.step)}>
                  Edit
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// --- gating -------------------------------------------------------------------------------------

const STEP_FIELDS: Record<number, string[]> = {
  0: ["age", "dependents", "risk tolerance", "investing experience"],
  1: ["annual gross income", "monthly essential expenses", "monthly discretionary"],
};

function stepBlockers(profile: ProfileDraft, step: number): string[] {
  const wanted = STEP_FIELDS[step];
  if (!wanted) return [];
  return REQUIRED_FIELDS.filter((f) => wanted.includes(f.label) && !f.filled(profile)).map(
    (f) => f.label,
  );
}

function nearestExperience(value: number | null): number | "" {
  if (value === null) return "";
  return EXPERIENCE_LEVELS.reduce((best, l) =>
    Math.abs(l.value - value) < Math.abs(best.value - value) ? l : best,
  ).value;
}
