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
  // Long enough that the near-term guardrail stays quiet, so the demo turns on concentration —
  // which is where the two lenses actually disagree.
  horizon_years: 12,
  investable_cash: 9_800,
  risk_tolerance: "moderate_aggressive",
  self_reported_experience: 0.45,
  notes: "Most of this is one position I have held for years and feel attached to.",
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
