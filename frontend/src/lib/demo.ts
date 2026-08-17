/**
 * A sample household, so someone can see the product before deciding to type their finances in.
 *
 * Three rules this has to obey, and they are the reason it is a separate file rather than a
 * default value on the form:
 *
 *   1. It is never presented as the user's. Every screen showing it says so.
 *   2. It is never persisted. Autosave is suppressed in demo mode, so a signed-in visitor who
 *      pokes at the demo does not come back tomorrow to someone else's balance sheet.
 *   3. It is copied only on an explicit action, and that action ends demo mode.
 *
 * The figures are deliberately a household with a real, visible tension — a large concentrated
 * position, a high-APR card, and a two-year goal — because a demo where nothing is wrong shows
 * none of what the product does.
 */

import type { HoldingDraft, ProfileDraft } from "./draft";

export const DEMO_LABEL = "Sample household — not your data";

export const DEMO_PROFILE: ProfileDraft = {
  age: 38,
  dependents: 1,
  income: { annual_gross: 165000, employer_match_pct: 0.04 },
  expenses: { monthly_essential: 5400, monthly_discretionary: 1800 },
  debts: [
    {
      name: "Credit card",
      balance: 14200,
      apr: 0.229,
      minimum_monthly_payment: 420,
    },
    {
      name: "Car loan",
      balance: 18600,
      apr: 0.054,
      minimum_monthly_payment: 390,
    },
  ],
  assets: [
    { name: "Checking", value: 9800, account_type: "cash", is_liquid: true },
    { name: "Emergency savings", value: 21500, account_type: "cash", is_liquid: true },
    { name: "401(k)", value: 142000, account_type: "traditional_401k", is_liquid: false },
  ],
  goals: [
    { name: "Home down payment", goal_type: "home_purchase", years_until_needed: 2, priority: 1 },
    { name: "Retirement", goal_type: "retirement", years_until_needed: 27, priority: 2 },
  ],
  risk_tolerance: "moderate",
  self_reported_experience: 0.4,
  notes: "",
};

export const DEMO_HOLDINGS: HoldingDraft[] = [
  { symbol: "NVDA", asset_class: "us_equity", quantity: 420, market_value: 77_280 },
  { symbol: "VTI", asset_class: "us_equity", quantity: 310, market_value: 92_070 },
  { symbol: "VXUS", asset_class: "intl_developed_equity", quantity: 480, market_value: 30_240 },
  { symbol: "BND", asset_class: "bonds", quantity: 380, market_value: 27_740 },
  { symbol: "AAPL", asset_class: "us_equity", quantity: 95, market_value: 21_090 },
];

export const DEMO_QUESTION =
  "Should I sell some NVDA to pay off my credit card, or keep the position and pay it down from cash flow?";

/** A fresh copy every time: the drafts are mutated by the editors. */
export function demoProfile(): ProfileDraft {
  return structuredClone(DEMO_PROFILE);
}

export function demoHoldings(): HoldingDraft[] {
  return structuredClone(DEMO_HOLDINGS);
}
