/**
 * The form's own shape, and the one-way conversion into what the API accepts.
 *
 * The API types in types.ts mirror the backend's Pydantic models, where every number is a
 * number. A form cannot use those directly: an empty field is not zero, and pre-filling zero to
 * satisfy the type is exactly how a made-up figure ends up in someone's analysis. So the draft
 * carries `number | null`, blank means blank, and `toProfileInput` returns null until the
 * answers it cannot invent are actually present.
 */

import type { PortfolioInput, ProfileInput } from "../types";

export interface DebtDraft {
  name: string;
  balance: number | null;
  apr: number | null;
  minimum_monthly_payment: number | null;
}

export interface AssetDraft {
  name: string;
  value: number | null;
  account_type: string;
  is_liquid: boolean;
}

export interface GoalDraft {
  name: string;
  goal_type: string;
  years_until_needed: number | null;
  priority: number | null;
}

export interface HoldingDraft {
  symbol: string;
  asset_class: string;
  quantity: number | null;
  market_value: number | null;
}

export interface ProfileDraft {
  age: number | null;
  dependents: number | null;
  income: { annual_gross: number | null; employer_match_pct: number | null };
  expenses: { monthly_essential: number | null; monthly_discretionary: number | null };
  debts: DebtDraft[];
  assets: AssetDraft[];
  goals: GoalDraft[];
  /** "" until the user picks one — there is no defensible default risk tolerance. */
  risk_tolerance: string;
  self_reported_experience: number | null;
  notes: string;
}

export const EMPTY_PROFILE: ProfileDraft = {
  age: null,
  dependents: null,
  income: { annual_gross: null, employer_match_pct: null },
  expenses: { monthly_essential: null, monthly_discretionary: null },
  debts: [],
  assets: [],
  goals: [],
  risk_tolerance: "",
  self_reported_experience: null,
  notes: "",
};

export const EMPTY_PORTFOLIO: { holdings: HoldingDraft[] } = { holdings: [] };

/**
 * The answers that cannot be guessed without changing the result. Employer match is not among
 * them: blank genuinely means "no match", which is a real answer rather than an assumption.
 */
export const REQUIRED_FIELDS: { label: string; filled: (d: ProfileDraft) => boolean }[] = [
  { label: "age", filled: (d) => d.age !== null },
  { label: "dependents", filled: (d) => d.dependents !== null },
  { label: "annual gross income", filled: (d) => d.income.annual_gross !== null },
  { label: "monthly essential expenses", filled: (d) => d.expenses.monthly_essential !== null },
  { label: "monthly discretionary", filled: (d) => d.expenses.monthly_discretionary !== null },
  { label: "risk tolerance", filled: (d) => d.risk_tolerance !== "" },
  { label: "investing experience", filled: (d) => d.self_reported_experience !== null },
];

export function missingFields(draft: ProfileDraft): string[] {
  return REQUIRED_FIELDS.filter((f) => !f.filled(draft)).map((f) => f.label);
}

/** The profile the API accepts, or null while a required answer is still missing. */
export function toProfileInput(draft: ProfileDraft): ProfileInput | null {
  if (missingFields(draft).length > 0) return null;

  return {
    age: draft.age!,
    dependents: draft.dependents!,
    income: {
      annual_gross: draft.income.annual_gross!,
      annual_net: null,
      stability: 0.8,
      employer_match_pct: draft.income.employer_match_pct ?? 0,
    },
    expenses: {
      monthly_essential: draft.expenses.monthly_essential!,
      monthly_discretionary: draft.expenses.monthly_discretionary!,
    },
    // A half-typed row is dropped rather than submitted as a zero: an unnamed debt of $0 is not
    // a fact about anyone's finances, and including it would put it in the report.
    debts: draft.debts
      .filter((d) => d.name.trim() !== "")
      .map((d) => ({
        name: d.name.trim(),
        balance: d.balance ?? 0,
        apr: d.apr ?? 0,
        minimum_monthly_payment: d.minimum_monthly_payment ?? 0,
      })),
    assets: draft.assets
      .filter((a) => a.name.trim() !== "")
      .map((a) => ({
        name: a.name.trim(),
        value: a.value ?? 0,
        account_type: a.account_type,
        is_liquid: a.is_liquid,
      })),
    goals: draft.goals
      .filter((g) => g.name.trim() !== "" && g.years_until_needed !== null)
      .map((g) => ({
        name: g.name.trim(),
        goal_type: g.goal_type,
        years_until_needed: g.years_until_needed!,
        priority: g.priority ?? 3,
      })),
    risk_tolerance: draft.risk_tolerance,
    self_reported_experience: draft.self_reported_experience!,
    notes: draft.notes,
  };
}

/** Holdings worth analyzing: named, and worth something. */
export function toPortfolioInput(holdings: HoldingDraft[]): PortfolioInput {
  return {
    holdings: holdings
      .filter((h) => h.symbol.trim() !== "" && (h.market_value ?? 0) > 0)
      .map((h) => ({
        symbol: h.symbol.trim().toUpperCase(),
        asset_class: h.asset_class,
        quantity: h.quantity,
        market_value: h.market_value!,
      })),
  };
}

/** Fill the form from what the server has stored. The inverse of `toProfileInput`. */
export function fromProfileInput(profile: ProfileInput): ProfileDraft {
  return {
    age: profile.age,
    dependents: profile.dependents,
    income: {
      annual_gross: profile.income.annual_gross,
      // Zero round-trips as blank: the field's placeholder already says blank means no match,
      // so showing a stored 0 would be the same answer written twice.
      employer_match_pct: profile.income.employer_match_pct || null,
    },
    expenses: {
      monthly_essential: profile.expenses.monthly_essential,
      monthly_discretionary: profile.expenses.monthly_discretionary,
    },
    debts: profile.debts.map((d) => ({
      name: d.name,
      balance: d.balance,
      apr: d.apr,
      minimum_monthly_payment: d.minimum_monthly_payment,
    })),
    assets: profile.assets.map((a) => ({
      name: a.name,
      value: a.value,
      account_type: a.account_type,
      is_liquid: a.is_liquid,
    })),
    goals: profile.goals.map((g) => ({
      name: g.name,
      goal_type: g.goal_type,
      years_until_needed: g.years_until_needed,
      priority: g.priority,
    })),
    risk_tolerance: profile.risk_tolerance,
    self_reported_experience: profile.self_reported_experience,
    notes: profile.notes,
  };
}

export function fromPortfolioInput(portfolio: PortfolioInput | null): HoldingDraft[] {
  if (!portfolio) return [];
  return portfolio.holdings.map((h) => ({
    symbol: h.symbol,
    asset_class: h.asset_class,
    quantity: h.quantity ?? null,
    market_value: h.market_value,
  }));
}

/** Parse an input's value, treating an empty field as absent rather than as zero. */
export function num(value: string): number | null {
  if (value.trim() === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/** Render a nullable number for a controlled input. */
export function str(value: number | null): string {
  return value === null ? "" : String(value);
}
