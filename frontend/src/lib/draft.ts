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

export interface HoldingDraft {
  symbol: string;
  asset_class: string;
  quantity: number | null;
  market_value: number | null;
}

export interface ProfileDraft {
  age: number | null;
  /** When the money is needed. The one input the house's hard constraint turns on. */
  horizon_years: number | null;
  /** Account cash available to deploy — not a household cash position. */
  investable_cash: number | null;
  /** "" until the user picks one — there is no defensible default risk tolerance. */
  risk_tolerance: string;
  self_reported_experience: number | null;
  notes: string;
}

export const EMPTY_PROFILE: ProfileDraft = {
  age: null,
  horizon_years: null,
  investable_cash: null,
  risk_tolerance: "",
  self_reported_experience: null,
  notes: "",
};

export const EMPTY_PORTFOLIO: { holdings: HoldingDraft[] } = { holdings: [] };

/**
 * The answers that cannot be guessed without changing the result.
 *
 * Deployable cash is not among them: blank genuinely means "nothing spare in the account",
 * which is a real answer rather than an assumption, and it is treated as zero.
 */
export const REQUIRED_FIELDS: { label: string; filled: (d: ProfileDraft) => boolean }[] = [
  { label: "age", filled: (d) => d.age !== null },
  { label: "when you need this money", filled: (d) => d.horizon_years !== null },
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
    currency: "USD",
    horizon_years: draft.horizon_years!,
    investable_cash: draft.investable_cash ?? 0,
    risk_tolerance: draft.risk_tolerance,
    self_reported_experience: draft.self_reported_experience!,
    notes: draft.notes.trim(),
  };
}

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
    horizon_years: profile.horizon_years,
    // Zero round-trips as blank: the field already says blank means nothing spare, so showing a
    // stored 0 would be the same answer written twice.
    investable_cash: profile.investable_cash || null,
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
